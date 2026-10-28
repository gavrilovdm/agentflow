"""Research cards: «найди материалы про X» → files in the library's inbox.

A research card goes through the same columns as code, with each stage's craft swapped
for the hub's research playbook:

- planner: a search plan (queries, what counts as interesting, how many findings) instead
  of a spec and tasks; research needs no approval, so it moves on at once;
- developer → the researcher: an agent with web_search and fetch_url finds sources and
  summarises each in its own words; code writes one file per finding into
  `<library>/inbox/` by the library's contract, drops what the library already has, and
  commits `research: <topic> (+N)` to its main branch;
- tester: checks the files without the researcher's history — the contract's fields, that
  each link opens, that no URL is in the library twice — and each Definition of Done item
  against the files. Proven → Смержено: research has no PR, so it skips Готово к мержу.

    library_repo (LIBRARY_REPO) names the library; its own AGENTS.md is the contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agentflow.agents.web_tools import HEADERS, make_web_tools
from agentflow.board import DOING, MERGED, Board, Card, Report, Thread
from agentflow.config import TargetRepo, get_settings
from agentflow.integrations import workspace as ws
from agentflow.models import chat_model, structured
from agentflow.schemas import Spec, Task
from agentflow.skills import role_section

log = logging.getLogger(__name__)

RESEARCHER = "researcher"
FILE_LINK = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/(inbox/[^\s,)]+\.md)")
REQUIRED = ("title", "url", "kind", "type", "tags", "added", "found_by")
MAX_SEARCH_CALLS = 30


# ─── planner ────────────────────────────────────────────────────────────────


class ResearchPlan(BaseModel):
    title: str = Field(description="The topic in a few words, in the author's language")
    goal: str = Field(description="What the author wants to learn or collect, 1-2 sentences")
    queries: list[str] = Field(min_length=2, description="Web searches to run, specific, in the languages that fit")
    criteria: str = Field(description="What makes a source worth keeping for this author; what to skip")
    count: int = Field(ge=1, le=15, description="How many findings to bring")
    out_of_scope: list[str] = Field(default_factory=list)


PLAN_SYSTEM = """You plan a web research task for one person's knowledge library. From the card,
write the searches to run, what counts as a source worth keeping, and how many to bring.
Use only what the card asks for; the Definition of Done is binding."""


async def make_research_plan(prompt: str, previous: Any, feedback: str | None, workspace: Path) -> Any:
    """The planner's research variant: same signature as planner.make_plan, a Plan out."""
    from agentflow.stages.planner import Plan

    messages = [SystemMessage(PLAN_SYSTEM + role_section("researcher")), HumanMessage(prompt)]
    if feedback:
        messages.append(HumanMessage(f"The previous plan was rejected: {feedback}"))
    rp = await structured(get_settings().orchestrator_model, ResearchPlan).ainvoke(messages)
    assert isinstance(rp, ResearchPlan)
    dod = _dod(prompt) or [f"{rp.count} findings in the library inbox"]
    spec = Spec(
        id=f"research-{uuid.uuid4().hex[:8]}",
        title=rp.title,
        goal=rp.goal,
        constraints=[rp.criteria],
        # The contract always applies; Spec also needs two criteria, and a card may have one item.
        acceptance_criteria=[
            *dod,
            "each finding is one inbox file by the library contract, none already in the library",
        ],
        technical_notes="",
        out_of_scope=rp.out_of_scope,
    )
    task = Task(
        id="task-research",
        title=f"Найти материалы: {rp.title}",
        description="Queries:\n" + "\n".join(f"- {q}" for q in rp.queries) + f"\n\nKeep: {rp.criteria}",
        target_files=["inbox/"],
        definition_of_done=f"{rp.count} findings, each a file in inbox/ by the library contract",
        interface=f"count={rp.count}",
    )
    return Plan(spec=spec, tasks=[task])


def _dod(text: str) -> list[str]:
    section = text.split("## Definition of Done", 1)[-1].split("\n## ", 1)[0] if "## Definition of Done" in text else ""
    return [m.group(1).strip() for m in re.finditer(r"^- \[[ xX]\] (.+)$", section, re.M)]


# ─── researcher (the developer of a research card) ──────────────────────────


class Finding(BaseModel):
    title: str = Field(description="The source's own title")
    url: str
    type: Literal["article", "video", "repo", "tool", "paper", "thread", "note"]
    tags: list[str] = Field(description="2-5 lowercase tags")
    summary: str = Field(
        description="2-5 sentences in the card's language, in your own words: what it is and why it helps"
    )


class Findings(BaseModel):
    items: list[Finding]


SEARCH_SYSTEM = """You research a topic on the web for one person's knowledge library.
Run the planned searches, open the promising results with fetch_url, and keep only sources
that meet the criteria. Summarise each in your own words; never copy the source's text.
Skip anything listed under "Already in the library". Return the findings."""

Researcher = Callable[[str, list[str]], Awaitable[Findings]]


async def search(brief: str, known: list[str]) -> Findings:
    s = get_settings()
    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=MAX_SEARCH_CALLS, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=2),
    ]
    agent = create_agent(
        chat_model(s.orchestrator_model, forces_tool_choice=True),
        tools=make_web_tools(),
        system_prompt=SEARCH_SYSTEM + role_section("researcher"),
        middleware=middleware,
        response_format=ToolStrategy(Findings),
        name="researcher",
    )
    payload: Any = {"messages": [HumanMessage(brief + "\n\n## Already in the library\n" + "\n".join(known[-200:]))]}
    out = await agent.ainvoke(payload, config={"tags": ["researcher"], "recursion_limit": MAX_SEARCH_CALLS * 3})
    result = out.get("structured_response")
    if not isinstance(result, Findings):
        raise RuntimeError("the researcher returned no findings")
    return result


def norm_url(url: str) -> str:
    """The library's dedup key: no utm_* parameters, no anchor, no trailing slash."""
    base, _, query = url.split("#", 1)[0].partition("?")
    kept = "&".join(p for p in query.split("&") if p and not p.startswith("utm_"))
    return (base.rstrip("/") + (f"?{kept}" if kept else "")).lower()


def library_urls(root: Path) -> dict[str, list[str]]:
    """Normalised URL → files that cite it, over inbox/ and topics/."""
    out: dict[str, list[str]] = {}
    for p in [*root.glob("inbox/*.md"), *root.glob("topics/**/*.md")]:
        if m := re.search(r"^url:\s*(\S+)", p.read_text(errors="ignore"), re.M):
            out.setdefault(norm_url(m.group(1)), []).append(str(p.relative_to(root)))
    return out


def slug(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()[:6]
    return "-".join(words) or "finding"


def render_finding(f: Finding, card: Card, today: str) -> str:
    tags = ", ".join(f.tags)
    title = f.title.replace('"', "'")
    return (
        f'---\ntitle: "{title}"\nurl: {f.url}\nkind: finding\ntype: {f.type}\nproject: \ntags: [{tags}]\n'
        f"added: {today}\nfound_by: research\ntopic: \nrelated: []\n---\n\n{f.summary.strip()}\n\n"
        f"Карточка: {card.url}\n"
    )


def write_findings(root: Path, findings: Findings, card: Card, today: str) -> list[str]:
    """New files for findings the library does not have yet; returns their paths."""
    have = library_urls(root)
    written = []
    for f in findings.items:
        key = norm_url(f.url)
        if key in have:
            continue
        rel = f"inbox/{today}-{slug(f.title)}.md"
        n = 2
        while (root / rel).exists():
            rel, n = f"inbox/{today}-{slug(f.title)}-{n}.md", n + 1
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(render_finding(f, card, today))
        have[key] = [rel]
        written.append(rel)
    return written


def library_target() -> TargetRepo:
    return TargetRepo.parse(get_settings().library_repo, "main")


@contextlib.asynccontextmanager
async def library_checkout() -> AsyncIterator[Path]:
    target = library_target()
    path = await asyncio.to_thread(ws.prepare_workspace, target, target.base_branch)
    try:
        yield path
    finally:
        ws.cleanup_workspace(path, target)


Checkout = Callable[[], AbstractAsyncContextManager[Path]]


def brief(thread: Thread, plan: Any, findings_report: str | None) -> str:
    task = plan.tasks[0]
    out = f"# {plan.spec.title}\n\n{plan.spec.goal}\n\n## Plan\n{task.description}\n\nBring: {task.interface}\n"
    out += "\n## Definition of Done\n" + "\n".join(f"- {d}" for d in plan.spec.acceptance_criteria)
    if findings_report:
        out += f"\n\n## The tester sent the last batch back\n{findings_report}\nFix what it found."
    return out


async def research(
    board: Board,
    card: Card,
    thread: Thread,
    plan: Any,
    findings_report: str | None = None,
    researcher: Researcher | None = None,
    checkout: Checkout | None = None,
    push: bool = True,
) -> tuple[str, Report]:
    """The researcher's pass: find, write, commit, push. Returns where the card goes."""
    async with (checkout or library_checkout)() as root:
        known = sorted(library_urls(root))
        findings = await (researcher or search)(brief(thread, plan, findings_report), known)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        written = write_findings(root, findings, card, today)
        if not written:
            return "Блокировка", Report(
                agent=RESEARCHER, summary=f"новых находок нет: {len(findings.items)} найдено, все уже в библиотеке"
            )
        await asyncio.to_thread(ws.commit, root, written, f"research: {plan.spec.title} (+{len(written)})")
        if push:
            await asyncio.to_thread(ws.push, root, "main")
    repo = get_settings().library_repo
    return "Тестируется", Report(
        agent=RESEARCHER,
        summary=f"находок: {len(written)} в {repo}/inbox",
        next_step="проверить контракт, ссылки, дубли и пункты DoD",
        artifacts=[f"https://github.com/{repo}/blob/main/{p}" for p in written],
    )


# ─── tester ─────────────────────────────────────────────────────────────────


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    head = text[4:].split("\n---", 1)[0]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.splitlines()) if k.strip()}


def contract_problems(rel: str, text: str, urls: dict[str, list[str]]) -> list[str]:
    fm = frontmatter(text)
    problems = [f"нет поля {k}" for k in REQUIRED if not fm.get(k) or fm.get(k) == "[]"]
    if not re.fullmatch(r"inbox/\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md", rel):
        problems.append("имя не по контракту inbox/<YYYY-MM-DD>-<slug>.md")
    body = text.split("\n---", 1)[-1].split("\n---\n", 1)[-1].strip()
    if len(re.findall(r"[.!?…](\s|$)", body.split("Карточка:")[0])) < 2:
        problems.append("выжимка короче двух предложений")
    if url := fm.get("url"):
        others = [p for p in urls.get(norm_url(url), []) if p != rel]
        if others:
            problems.append(f"дубль: {url} уже в {', '.join(others)}")
    return problems


async def link_opens(url: str) -> bool:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as http:
        try:
            resp = await http.get(url)
        except httpx.HTTPError:
            return False
    return resp.status_code < 400


class ItemVerdict(BaseModel):
    item: str
    passed: bool
    evidence: str = Field(description="Which files show it, quoted briefly; or what is missing")


class Verdicts(BaseModel):
    items: list[ItemVerdict]


Judge = Callable[[list[str], dict[str, str]], Awaitable[Verdicts]]


async def judge_dod(items: list[str], files: dict[str, str]) -> Verdicts:
    corpus = "\n\n".join(f"### {p}\n{t}" for p, t in files.items())
    numbered = "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))
    out = await structured(get_settings().orchestrator_model, Verdicts).ainvoke(
        [
            SystemMessage(
                "You check a research card's Definition of Done against the files it produced. You did not "
                "write them. One verdict per item, in order, item text verbatim." + role_section("tester")
            ),
            HumanMessage(f"## Definition of Done\n{numbered}\n\n## Files\n{corpus}"),
        ]
    )
    assert isinstance(out, Verdicts)
    return out


@dataclass
class Checked:
    item: str
    passed: bool
    evidence: str


async def check(
    card: Card,
    thread: Thread,
    items: list[str],
    judge: Judge | None = None,
    checkout: Checkout | None = None,
    opens: Callable[[str], Awaitable[bool]] | None = None,
) -> tuple[str, Report]:
    judge, checkout, opens = judge or judge_dod, checkout or library_checkout, opens or link_opens
    paths = sorted({m.group(1) for c in thread.comments for m in FILE_LINK.finditer(c.body)})
    if not paths:
        return DOING, Report(agent="tester", summary="в отчёте researcher нет файлов")
    async with checkout() as root:
        urls = library_urls(root)
        files = {p: (root / p).read_text() if (root / p).is_file() else "" for p in paths}
    checked = []
    for p, text in files.items():
        problems = ["файла нет в библиотеке"] if not text else contract_problems(p, text, urls)
        url = frontmatter(text).get("url", "")
        if url and not await opens(url):
            problems.append(f"ссылка не открывается: {url}")
        checked.append(
            Checked(f"`{p}` по контракту", not problems, "; ".join(problems) or "поля, имя, ссылка, дублей нет")
        )
    verdicts = (await judge(items, files)).items if items else []
    checked += [Checked(v.item, v.passed, v.evidence) for v in verdicts]
    lines = "\n".join(f"- [{'x' if c.passed else ' '}] {c.item} — {c.evidence}" for c in checked)
    if all(c.passed for c in checked):
        return MERGED, Report(agent="tester", summary=f"все проверки прошли ({len(checked)})\n\n{lines}")
    failed = sum(not c.passed for c in checked)
    return DOING, Report(
        agent="tester",
        summary=f"не прошло проверок: {failed} из {len(checked)}\n\n{lines}",
        next_step="исправить или заменить находки с неотмеченными пунктами",
    )
