"""Research cards: plan → findings in the library inbox → contract, link and DoD checks → done."""

import contextlib
from pathlib import Path

from git import Repo

from agentflow import board as b
from agentflow.agents.web_tools import parse_results
from agentflow.board import Card, Comment, Report, Thread
from agentflow.schemas import Spec, Task
from agentflow.stages import research
from agentflow.stages.planner import Plan
from agentflow.stages.research import Finding, Findings, ItemVerdict, Verdicts

BODY = (
    "## Цель\nматериалы про LangGraph interrupts\n\n## Definition of Done\n- [ ] минимум 2 находки\n\n## Контекст\n—\n"
)
EXISTING = '---\ntitle: "Old"\nurl: https://docs.example.com/interrupts/\nkind: finding\n---\n\nOld.\n'


def _card():
    return Card(item_id="IT", issue_id="I", number=20, repo="o/sandbox", url="https://x/20", title="Research",
                status=b.DOING, type="research")  # fmt: skip


def _plan():
    spec = Spec(id="s", title="LangGraph interrupts", goal="g", constraints=["primary sources"],
                acceptance_criteria=["минимум 2 находки", "по контракту"], technical_notes="", out_of_scope=[])  # fmt: skip
    task = Task(id="task-research", title="t", description="Queries:\n- langgraph interrupt", target_files=["inbox/"],
                definition_of_done="2 findings", interface="count=2")  # fmt: skip
    return Plan(spec=spec, tasks=[task])


def _finding(url, title="LangGraph Interrupts docs"):
    return Finding(title=title, url=url, type="article", tags=["langgraph", "agents"],
                   summary="Официальная страница о прерываниях. Объясняет, как граф ждёт человека. Полезна для HITL.")  # fmt: skip


def _library(tmp_path: Path) -> Path:
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "2026-10-01-old.md").write_text(EXISTING)
    r = Repo.init(tmp_path)
    r.index.add(["inbox/2026-10-01-old.md"])
    r.index.commit("init")
    return tmp_path


def _checkout(root):
    @contextlib.asynccontextmanager
    async def co():
        yield root

    return co


def test_duckduckgo_results_unwrap_to_the_source():
    page = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.x.com%2Fa&amp;rut=1">A <b>doc</b></a>'
            '<a class="result__snippet" href="#">The <b>snippet</b></a>')  # fmt: skip
    assert parse_results(page) == [("https://docs.x.com/a", "A doc", "The snippet")]


def test_findings_already_in_the_library_are_dropped_and_files_follow_the_contract(tmp_path):
    root = _library(tmp_path)
    found = Findings(items=[
        _finding("https://docs.example.com/interrupts?utm_source=x#top"),  # the library has it
        _finding("https://blog.example.com/hitl", title="Human in the loop!"),
        _finding("https://blog.example.com/hitl2", title="Human in the loop!"),
    ])  # fmt: skip
    written = research.write_findings(root, found, _card(), "2026-10-28")
    assert written == ["inbox/2026-10-28-human-in-the-loop.md", "inbox/2026-10-28-human-in-the-loop-2.md"]
    urls = research.library_urls(root)
    for rel in written:
        assert research.contract_problems(rel, (root / rel).read_text(), urls) == []


async def test_researcher_commits_findings_and_hands_them_to_the_tester(tmp_path):
    root = _library(tmp_path)
    seen = {}

    async def researcher(brief, known):
        seen["brief"], seen["known"] = brief, known
        return Findings(items=[_finding("https://blog.example.com/hitl")])

    th = Thread(title="t", body=BODY, author="u")
    to, report = await research.research(None, _card(), th, _plan(), None, researcher, _checkout(root), push=False)
    assert to == b.TESTING and report.artifacts[0].endswith("/blob/main/inbox/" + Path(report.artifacts[0]).name)
    assert "langgraph interrupt" in seen["brief"] and "https://docs.example.com/interrupts" in seen["known"]
    assert "research: LangGraph interrupts (+1)" in Repo(root).head.commit.message

    # nothing new: the card goes to a person rather than to a tester with nothing to check
    to, _ = await research.research(None, _card(), th, _plan(), None, researcher, _checkout(root), push=False)
    assert to == b.BLOCKED


async def test_tester_closes_a_good_batch_and_sends_back_a_dead_link_or_a_duplicate(tmp_path):
    root = _library(tmp_path)
    written = research.write_findings(
        root, Findings(items=[_finding("https://blog.example.com/hitl")]), _card(), "2026-10-28"
    )
    links = Report(agent="researcher", summary="s", artifacts=[f"https://github.com/o/library/blob/main/{written[0]}"])
    th = Thread(title="t", body=BODY, author="u",
                comments=[Comment(author="bot", body=b.render_report(links, b.DOING, b.TESTING, 0), created_at="1")])  # fmt: skip

    async def judge(items, files):
        return Verdicts(items=[ItemVerdict(item=i, passed=True, evidence=next(iter(files))) for i in items])

    async def opens(url):
        return True

    to, report = await research.check(_card(), th, ["минимум 2 находки"], judge, _checkout(root), opens)
    assert to == b.MERGED and "[x] минимум 2 находки" in report.summary
    assert b.allowed(b.TESTING, b.MERGED)  # research has no PR to wait for

    async def dead(url):
        return False

    to, report = await research.check(_card(), th, [], judge, _checkout(root), dead)
    assert to == b.DOING and "does not open" in report.summary

    (root / "inbox" / "2026-10-28-copy.md").write_text((root / written[0]).read_text())
    to, report = await research.check(_card(), th, [], judge, _checkout(root), opens)
    assert to == b.DOING and "duplicate" in report.summary


def test_research_card_is_planned_without_a_repo_to_explore(monkeypatch):
    from agentflow.stages import planner

    calls = []

    async def fake(prompt, previous, feedback, workspace):
        calls.append(workspace)
        return _plan()

    monkeypatch.setattr(research, "make_research_plan", fake)

    class FB:
        async def thread(self, card):
            return Thread(title="t", body=BODY, author="u")

        async def comment(self, card, body):
            pass

        async def sub_issues(self, card):
            return []

        async def add_sub_issue(self, card, title, body):
            return b.SubIssue(issue_id="S", number=1, url="u", title=title, body=body)

        async def move(self, card, to, report):
            calls.append(to)

    import asyncio

    out = asyncio.run(planner.process(FB(), _card().model_copy(update={"approval": "none", "status": b.PLANNING})))
    assert out.kind == "approved" and calls == [Path("."), b.DOING]


async def test_done_research_card_closes_its_issue(monkeypatch):
    from agentflow.stages import tester

    async def passed(card, thread, items):
        return b.MERGED, Report(agent="tester", summary="ok")

    monkeypatch.setattr(research, "check", passed)
    calls = []

    class FB:
        async def thread(self, card):
            return Thread(title="t", body=BODY, author="u")

        async def move(self, card, to, report):
            calls.append(("move", to))
            return card.model_copy(update={"status": to})

        async def close_issue(self, issue_id):
            calls.append(("close", issue_id))

    out = await tester.process(FB(), _card().model_copy(update={"status": b.TESTING}))
    assert out.column == b.MERGED and calls == [("move", b.MERGED), ("close", "I")]
