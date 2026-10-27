import json

import httpx
import pytest

from agentflow import board as b
from agentflow.board import Board, Card, Report, TransitionRefused


class FakeGitHub:
    """A GraphQL endpoint holding one project; records every mutation in order."""

    def __init__(self, fields=None):
        self.calls: list[tuple[str, dict]] = []
        self.fields = fields if fields is not None else self._full()
        self.values: dict[tuple[str, str], object] = {}
        self.items: list[dict] = []

    @staticmethod
    def _full():
        out = []
        for name, (dtype, options) in b.FIELDS.items():
            f = {"id": f"F-{name}", "name": name, "dataType": dtype}
            if dtype == "SINGLE_SELECT":
                f["options"] = [{"id": f"O-{o}", "name": o} for o, _ in options]
            out.append(f)
        return out

    def _field(self, fid):
        return next(f for f in self.fields if f["id"] == fid)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q, v = body["query"], body["variables"]
        op = next(k for k in (
            "createProjectV2Field", "updateProjectV2Field", "clearProjectV2ItemFieldValue",
            "updateProjectV2ItemFieldValue", "createIssue", "addProjectV2ItemById", "addComment",
            "repository(owner", "items(first", "fields(first",
        ) if k in q)  # fmt: skip
        self.calls.append((op, v))
        if op == "fields(first":
            data = {"organization": {"projectV2": {"id": "P1", "fields": {"nodes": self.fields}}}}
        elif op == "createProjectV2Field":
            self.fields.append({"id": f"F-{v['n']}", "name": v["n"], "dataType": v["t"],
                                "options": [{"id": f"O-{o['name']}", "name": o["name"]} for o in v["o"] or []]})  # fmt: skip
            data = {}
        elif op == "updateProjectV2Field":
            self._field(v["f"])["options"] = [{"id": f"O-{o['name']}", "name": o["name"]} for o in v["o"]]
            data = {}
        elif op == "repository(owner":
            data = {"repository": {"id": "R1"}}
        elif op == "createIssue":
            data = {"createIssue": {"issue": {"id": "I1", "number": 7, "url": "https://github.com/o/r/issues/7"}}}
        elif op == "addProjectV2ItemById":
            data = {"addProjectV2ItemById": {"item": {"id": "IT1"}}}
        elif op == "updateProjectV2ItemFieldValue":
            f, val = self._field(v["f"]), v["v"]
            opt = val.get("singleSelectOptionId")
            name = next((o["name"] for o in f.get("options", []) if o["id"] == opt), None)
            self.values[(v["i"], f["name"])] = name if opt else val.get("text", val.get("number"))
            data = {}
        elif op == "clearProjectV2ItemFieldValue":
            self.values.pop((v["i"], self._field(v["f"])["name"]), None)
            data = {}
        elif op == "items(first":
            data = {"organization": {"projectV2": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.items}}}}  # fmt: skip
        else:
            data = {}
        return httpx.Response(200, json={"data": data})

    def mutations(self):
        return [op for op, _ in self.calls if not op.endswith("(first") and op != "repository(owner"]


def _board(gh: FakeGitHub) -> Board:
    return Board("t", "org", 1, transport=httpx.MockTransport(gh.handler))


def _card(status, attempts=0, blocked_reason=None):
    return Card(item_id="IT1", issue_id="I1", number=7, repo="o/r", url="u", title="t",
                status=status, attempts=attempts, blocked_reason=blocked_reason)  # fmt: skip


def test_transitions_go_one_column_forward_and_back_only_to_the_developer():
    assert b.allowed(b.BACKLOG, b.PLANNING) and b.allowed(b.READY, b.MERGED)
    assert not b.allowed(b.BACKLOG, b.DOING)  # no skipping a stage
    assert b.allowed(b.TESTING, b.DOING) and b.allowed(b.READY, b.DOING)
    assert not b.allowed(b.TESTING, b.PLANNING) and not b.allowed(b.MERGED, b.DOING)
    assert all(b.allowed(c, b.BLOCKED) for c in b.WORKING) and not b.allowed(b.MERGED, b.BLOCKED)
    assert b.allowed(b.BLOCKED, b.DOING) and not b.allowed(b.BLOCKED, b.MERGED)


async def test_setup_replaces_default_status_options_and_creates_missing_fields_once():
    default = [{"id": "F-Status", "name": "Status", "dataType": "SINGLE_SELECT",
                "options": [{"id": "x", "name": n} for n in ("Todo", "In Progress", "Done")]}]  # fmt: skip
    gh = FakeGitHub(fields=default)
    board = _board(gh)
    changed = await board.setup()
    assert "options of Status" in changed and "created Owner" in changed and len(changed) == len(b.FIELDS)
    assert [o["name"] for o in gh._field("F-Status")["options"]] == list(b.COLUMNS)
    assert await board.setup() == []  # idempotent


async def test_move_writes_the_report_before_the_status():
    """An agent dying between the two must leave the report, not a card in a new column
    with no explanation."""
    gh = FakeGitHub()
    card = await _board(gh).move(_card(b.DOING), b.TESTING, Report(agent="developer", summary="all green"))
    ops = gh.mutations()
    assert ops[0] == "addComment" and ops[1] == "updateProjectV2ItemFieldValue"
    comment = gh.calls[[op for op, _ in gh.calls].index("addComment")][1]["b"]
    assert "developer · Делается → Тестируется" in comment and "agentflow:report" in comment
    assert gh.values[("IT1", "Status")] == b.TESTING and card.status == b.TESTING and card.owner is None


async def test_third_return_blocks_the_card_instead_of_looping():
    gh = FakeGitHub()
    board = _board(gh)
    once = await board.move(_card(b.TESTING, attempts=1), b.DOING, Report(agent="tester", summary="DoD 2 fails"))
    assert once.status == b.DOING and once.attempts == 2 and gh.values[("IT1", "Attempts")] == 2
    third = await board.move(_card(b.TESTING, attempts=2), b.DOING, Report(agent="tester", summary="DoD 2 fails"))
    assert third.status == b.BLOCKED and third.attempts == 3
    assert "DoD 2 fails" in gh.values[("IT1", "Blocked reason")]
    # back out of Блокировка: the reason is cleared
    out = await board.move(third, b.DOING, Report(agent="human", summary="retry with hint"))
    assert out.blocked_reason is None and ("IT1", "Blocked reason") not in gh.values


async def test_refused_transition_writes_nothing():
    gh = FakeGitHub()
    with pytest.raises(TransitionRefused):
        await _board(gh).move(_card(b.BACKLOG), b.DOING, Report(agent="intake", summary="x"))
    assert gh.calls == []


async def test_create_task_opens_an_issue_from_the_template_in_backlog():
    gh = FakeGitHub()
    card = await _board(gh).create_task("o/r", "LinkedIn icon", "Show a LinkedIn link.", ["icon visible", "opens new tab"],
                                        source="telegram", reply_to="chat:1")  # fmt: skip
    body = next(v for op, v in gh.calls if op == "createIssue")["b"]
    assert "- [ ] icon visible\n- [ ] opens new tab" in body and "source=telegram reply_to=chat:1" in body
    assert gh.values[("IT1", "Status")] == b.BACKLOG and gh.values[("IT1", "Task type")] == "coding"
    assert card.number == 7 and card.status == b.BACKLOG
    with pytest.raises(b.BoardError):
        await _board(FakeGitHub()).create_task("o/r", "t", "g", [])


async def test_cards_reads_field_values_and_skips_drafts():
    gh = FakeGitHub()
    gh.items = [
        {"id": "IT1", "content": {"id": "I1", "number": 7, "title": "t", "url": "u", "repository": {"nameWithOwner": "o/r"}},
         "fieldValues": {"nodes": [{"name": b.TESTING, "field": {"name": "Status"}}, {"number": 2.0, "field": {"name": "Attempts"}},
                                   {"text": "tester", "field": {"name": "Owner"}}, {}]}},
        {"id": "D1", "content": {}, "fieldValues": {"nodes": []}},
    ]  # fmt: skip
    cards = await _board(gh).cards()
    assert len(cards) == 1 and cards[0].status == b.TESTING and cards[0].attempts == 2 and cards[0].owner == "tester"
    assert await _board(gh).cards(b.DOING) == []


async def test_mcp_create_task_refuses_a_repo_outside_the_whitelist(monkeypatch, tmp_path):
    from agentflow import mcp_server

    (tmp_path / "repos.toml").write_text('[[repo]]\nfull_name = "org/ok"\n')
    monkeypatch.setenv("REPOS_FILE", str(tmp_path / "repos.toml"))
    from agentflow.config import get_settings

    get_settings.cache_clear()
    r = await mcp_server.create_task("evil/repo", "t", "g", ["d"])
    assert r["status"] == "rejected"
