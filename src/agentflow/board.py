"""The task board: a GitHub Projects (v2) board where every task is an issue (a card).

Each column is a stage with its own agent; a card moves only through `Board.move`, which
checks the transition against `allowed`, writes the stage's report as an issue comment
and only then changes the Status field. The issue's comments are the card's history:
an agent of the next stage reads the card, not the previous agent's context.

    uv run python scripts/setup_board.py     # create the columns and fields once
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from agentflow.config import get_settings

log = logging.getLogger(__name__)

API = "https://api.github.com/graphql"

BACKLOG, PLANNING, DOING, TESTING, READY, MERGED, BLOCKED = (
    "Бэклог",
    "Планируется",
    "Делается",
    "Тестируется",
    "Готово к мержу",
    "Смержено",
    "Блокировка",
)
COLUMNS = (BACKLOG, PLANNING, DOING, TESTING, READY, MERGED, BLOCKED)
WORKING = (BACKLOG, PLANNING, DOING, TESTING, READY)
FORWARD = dict(zip(WORKING, COLUMNS[1:6], strict=True))
# A tester or a reviewer of the PR sends the work back to the developer, never further.
BACK = {TESTING: DOING, READY: DOING}
# A card sent back this many times stops looping and waits for a person with the history.
MAX_ATTEMPTS = 3

TaskType = Literal["coding", "research", "text"]
Priority = Literal["high", "normal", "low"]

# name → (data type, single-select options with GitHub colours)
FIELDS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "Status": (
        "SINGLE_SELECT",
        [
            (BACKLOG, "GRAY"),
            (PLANNING, "BLUE"),
            (DOING, "YELLOW"),
            (TESTING, "ORANGE"),
            (READY, "PURPLE"),
            (MERGED, "GREEN"),
            (BLOCKED, "RED"),
        ],
    ),
    # "Type" is reserved: GitHub keeps it for its own issue types.
    "Task type": ("SINGLE_SELECT", [("coding", "BLUE"), ("research", "PURPLE"), ("text", "GRAY")]),
    "Priority": ("SINGLE_SELECT", [("high", "RED"), ("normal", "GRAY"), ("low", "GRAY")]),
    "Approval": ("SINGLE_SELECT", [("full", "GRAY"), ("plan_only", "GRAY"), ("none", "GRAY")]),
    "Owner": ("TEXT", []),
    "Attempts": ("NUMBER", []),
    "Blocked reason": ("TEXT", []),
    "Run": ("TEXT", []),
}


def allowed(src: str, dst: str) -> bool:
    """One column forward, back to the developer from testing or review, into Блокировка
    from any working column, and out of it only to a working column."""
    if src == BLOCKED:
        return dst in WORKING
    return dst == FORWARD.get(src) or dst == BACK.get(src) or (dst == BLOCKED and src in WORKING)


class BoardError(RuntimeError):
    pass


class TransitionRefused(BoardError):
    pass


class Report(BaseModel):
    """What a stage's agent leaves on the card when it moves it. The next stage works from
    this alone, so it says what was found and what to do next, with links."""

    agent: str
    summary: str
    next_step: str = ""
    artifacts: list[str] = Field(default_factory=list)


class Card(BaseModel):
    item_id: str
    issue_id: str
    number: int
    repo: str
    url: str
    title: str
    status: str | None = None
    type: str | None = None
    priority: str | None = None
    owner: str | None = None
    attempts: int = 0
    blocked_reason: str | None = None
    run: str | None = None


class Comment(BaseModel):
    author: str
    body: str
    created_at: str


class Thread(BaseModel):
    title: str
    body: str
    author: str
    comments: list[Comment] = Field(default_factory=list)


CARD_MARKER = re.compile(r"<!-- agentflow:card source=(\S*) reply_to=(\S*) -->")


def render_body(
    goal: str, definition_of_done: list[str], context: str = "", source: str = "", reply_to: str = ""
) -> str:
    dod = "\n".join(f"- [ ] {item}" for item in definition_of_done)
    marker = f"<!-- agentflow:card source={source or 'unknown'} reply_to={reply_to or '-'} -->"
    return f"## Цель\n{goal}\n\n## Definition of Done\n{dod}\n\n## Контекст\n{context or '—'}\n\n{marker}\n"


def render_report(report: Report, src: str, dst: str, attempts: int) -> str:
    back = BACK.get(src) == dst
    head = f"### {report.agent} · {src} → {dst}" + (f" (возврат {attempts}/{MAX_ATTEMPTS})" if back else "")
    lines = [head, f"**Итог:** {report.summary}"]
    if report.next_step:
        lines.append(f"**Что сделать следующей стадии:** {report.next_step}")
    if report.artifacts:
        lines.append("**Артефакты:** " + ", ".join(report.artifacts))
    # Machine-readable line: the orchestrator counts time in columns and returns from it.
    lines.append(f"<!-- agentflow:report agent={report.agent} from={src} to={dst} attempts={attempts} -->")
    return "\n\n".join(lines)


_FIELD_VALUES = """
fieldValues(first: 20) { nodes {
  ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
} }
content { ... on Issue { id number title url repository { nameWithOwner } } }
"""


def _card(item: dict[str, Any]) -> Card | None:
    content = item.get("content") or {}
    if "number" not in content:  # a draft or a pull request: not a card
        return None
    values: dict[str, Any] = {}
    for v in item["fieldValues"]["nodes"]:
        name = (v.get("field") or {}).get("name")
        if name:
            values[name] = v.get("name", v.get("text", v.get("number")))
    return Card(
        item_id=item["id"],
        issue_id=content["id"],
        number=content["number"],
        repo=content["repository"]["nameWithOwner"],
        url=content["url"],
        title=content["title"],
        status=values.get("Status"),
        type=values.get("Task type"),
        priority=values.get("Priority"),
        owner=values.get("Owner") or None,
        attempts=int(values.get("Attempts") or 0),
        blocked_reason=values.get("Blocked reason") or None,
        run=values.get("Run") or None,
    )


class Board:
    def __init__(self, token: str, org: str, number: int, transport: httpx.AsyncBaseTransport | None = None):
        if not (token and org and number):
            raise BoardError("board is not configured: set GITHUB_BOARD_TOKEN, BOARD_ORG and BOARD_PROJECT")
        self.org, self.number = org, number
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, transport=transport, timeout=30)
        self._project: dict[str, Any] | None = None

    @classmethod
    def from_settings(cls) -> Board:
        s = get_settings()
        return cls(s.github_board_token, s.board_org, s.board_project)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _gql(self, query: str, **variables: Any) -> dict[str, Any]:
        resp = await self._http.post(API, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise BoardError("; ".join(e.get("message", str(e)) for e in body["errors"]))
        data: dict[str, Any] = body["data"]
        return data

    async def project(self, refresh: bool = False) -> dict[str, Any]:
        """Project id and its fields: name → {id, type, options: name → option id}."""
        if self._project is None or refresh:
            data = await self._gql(
                """query($org: String!, $n: Int!) { organization(login: $org) { projectV2(number: $n) { id
                  fields(first: 50) { nodes {
                    ... on ProjectV2FieldCommon { id name dataType }
                    ... on ProjectV2SingleSelectField { options { id name } } } } } } }""",
                org=self.org,
                n=self.number,
            )
            p = data["organization"]["projectV2"]
            fields = {
                f["name"]: {
                    "id": f["id"],
                    "type": f["dataType"],
                    "options": {o["name"]: o["id"] for o in f.get("options", [])},
                }
                for f in p["fields"]["nodes"]
                if f
            }
            self._project = {"id": p["id"], "fields": fields}
        return self._project

    async def setup(self) -> list[str]:
        """Create missing fields and make Status hold exactly the board's columns. Returns
        what changed; running it again changes nothing."""
        p = await self.project(refresh=True)
        changed = []
        for name, (dtype, options) in FIELDS.items():
            opts = [{"name": o, "color": c, "description": ""} for o, c in options]
            have = p["fields"].get(name)
            if have is None:
                await self._gql(
                    """mutation($p: ID!, $t: ProjectV2CustomFieldType!, $n: String!,
                       $o: [ProjectV2SingleSelectFieldOptionInput!]) {
                      createProjectV2Field(input: {projectId: $p, dataType: $t, name: $n, singleSelectOptions: $o}) {
                        projectV2Field { ... on ProjectV2FieldCommon { id } } } }""",
                    p=p["id"],
                    t=dtype,
                    n=name,
                    o=opts or None,
                )
                changed.append(f"created {name}")
            elif options and list(have["options"]) != [o for o, _ in options]:
                # Status comes with Todo / In Progress / Done; the board's columns replace them.
                await self._gql(
                    """mutation($f: ID!, $o: [ProjectV2SingleSelectFieldOptionInput!]) {
                      updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: $o}) {
                        projectV2Field { ... on ProjectV2FieldCommon { id } } } }""",
                    f=have["id"],
                    o=opts,
                )
                changed.append(f"options of {name}")
        if changed:
            await self.project(refresh=True)
        return changed

    async def _set(self, item_id: str, field: str, value: str | float | None) -> None:
        p = await self.project()
        f = p["fields"][field]
        if value is None or value == "":
            await self._gql(
                """mutation($p: ID!, $i: ID!, $f: ID!) {
                  clearProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f}) {
                    clientMutationId } }""",
                p=p["id"],
                i=item_id,
                f=f["id"],
            )
            return
        if f["type"] == "SINGLE_SELECT":
            v: dict[str, Any] = {"singleSelectOptionId": f["options"][value]}
        elif f["type"] == "NUMBER":
            v = {"number": float(value)}
        else:
            v = {"text": str(value)}
        await self._gql(
            """mutation($p: ID!, $i: ID!, $f: ID!, $v: ProjectV2FieldValue!) {
              updateProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f, value: $v}) {
                projectV2Item { id } } }""",
            p=p["id"],
            i=item_id,
            f=f["id"],
            v=v,
        )

    async def create_task(
        self,
        repo: str,
        title: str,
        goal: str,
        definition_of_done: list[str],
        context: str = "",
        type: TaskType = "coding",
        priority: Priority = "normal",
        source: str = "",
        reply_to: str = "",
    ) -> Card:
        """Open an issue in `repo` and put it in Бэклог."""
        if not definition_of_done:
            raise BoardError("a card needs at least one Definition of Done item")
        owner, _, name = repo.partition("/")
        data = await self._gql(
            "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { id } }", o=owner, n=name
        )
        issue = (
            await self._gql(
                """mutation($r: ID!, $t: String!, $b: String!) {
                  createIssue(input: {repositoryId: $r, title: $t, body: $b}) { issue { id number url } } }""",
                r=data["repository"]["id"],
                t=title,
                b=render_body(goal, definition_of_done, context, source, reply_to),
            )
        )["createIssue"]["issue"]
        p = await self.project()
        item_id = (
            await self._gql(
                """mutation($p: ID!, $c: ID!) { addProjectV2ItemById(input: {projectId: $p, contentId: $c}) {
                  item { id } } }""",
                p=p["id"],
                c=issue["id"],
            )
        )["addProjectV2ItemById"]["item"]["id"]
        for field, value in (("Status", BACKLOG), ("Task type", type), ("Priority", priority), ("Attempts", 0)):
            await self._set(item_id, field, value)
        return Card(
            item_id=item_id,
            issue_id=issue["id"],
            number=issue["number"],
            repo=repo,
            url=issue["url"],
            title=title,
            status=BACKLOG,
            type=type,
            priority=priority,
        )

    async def cards(self, status: str | None = None) -> list[Card]:
        """Cards on the board (issues only), optionally one column."""
        out: list[Card] = []
        cursor = None
        while True:
            data = await self._gql(
                """query($org: String!, $n: Int!, $after: String) { organization(login: $org) {
                  projectV2(number: $n) { items(first: 100, after: $after) {
                    pageInfo { hasNextPage endCursor } nodes { id """
                + _FIELD_VALUES
                + "} } } } }",
                org=self.org,
                n=self.number,
                after=cursor,
            )
            items = data["organization"]["projectV2"]["items"]
            out += [c for c in map(_card, items["nodes"]) if c and (status is None or c.status == status)]
            if not items["pageInfo"]["hasNextPage"]:
                return out
            cursor = items["pageInfo"]["endCursor"]

    async def thread(self, card: Card) -> Thread:
        """The issue as the author wrote it, with its comments: what a stage agent reads."""
        data = await self._gql(
            """query($id: ID!) { node(id: $id) { ... on Issue { title body author { login }
              comments(last: 100) { nodes { author { login } body createdAt } } } } }""",
            id=card.issue_id,
        )
        n = data["node"]
        return Thread(
            title=n["title"],
            body=n["body"] or "",
            author=(n.get("author") or {}).get("login", ""),
            comments=[
                Comment(author=(c.get("author") or {}).get("login", ""), body=c["body"], created_at=c["createdAt"])
                for c in n["comments"]["nodes"]
            ],
        )

    async def comment(self, card: Card, body: str) -> None:
        await self._gql(
            "mutation($s: ID!, $b: String!) { addComment(input: {subjectId: $s, body: $b}) { clientMutationId } }",
            s=card.issue_id,
            b=body,
        )

    async def update_issue(self, card: Card, title: str, body: str) -> None:
        await self._gql(
            "mutation($i: ID!, $t: String!, $b: String!) { updateIssue(input: {id: $i, title: $t, body: $b}) {"
            " clientMutationId } }",
            i=card.issue_id,
            t=title,
            b=body,
        )

    async def set_field(self, card: Card, field: str, value: str | float | None) -> None:
        await self._set(card.item_id, field, value)

    async def claim(self, card: Card, agent: str) -> bool:
        """Write `agent` into Owner if the card is free, then read it back. Not atomic: two
        agents writing at once both see a value; the read-back makes the loser step away."""
        if card.owner and card.owner != agent:
            return False
        await self._set(card.item_id, "Owner", agent)
        fresh = next((c for c in await self.cards() if c.item_id == card.item_id), None)
        return fresh is not None and fresh.owner == agent

    async def move(self, card: Card, to: str, report: Report) -> Card:
        """Move a card with its report. The comment goes first: an agent that dies between
        the two leaves the card in its old column with the report, which the orchestrator
        can finish; the other order left a card in a new column with no explanation."""
        src = card.status or BACKLOG
        if not allowed(src, to):
            raise TransitionRefused(f"{src} → {to} is not a transition on the board")
        attempts = card.attempts + (1 if BACK.get(src) == to else 0)
        reason = None
        if BACK.get(src) == to and attempts >= MAX_ATTEMPTS:
            reason = f"вернулась на доработку {attempts} раза: {report.summary}"
            to = BLOCKED
        elif to == BLOCKED:
            reason = report.summary
        await self._gql(
            "mutation($s: ID!, $b: String!) { addComment(input: {subjectId: $s, body: $b}) { clientMutationId } }",
            s=card.issue_id,
            b=render_report(report, src, to, attempts),
        )
        await self._set(card.item_id, "Status", to)
        if attempts != card.attempts:
            await self._set(card.item_id, "Attempts", attempts)
        await self._set(card.item_id, "Owner", None)
        if reason or card.blocked_reason:
            await self._set(card.item_id, "Blocked reason", reason)
        return card.model_copy(update={"status": to, "attempts": attempts, "owner": None, "blocked_reason": reason})
