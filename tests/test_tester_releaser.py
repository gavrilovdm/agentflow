"""Тестируется and Готово к мержу: the tester over a real repo and gate, the releaser over a fake board."""

import contextlib
import shutil
from pathlib import Path

import pytest

from agentflow import board as b
from agentflow.board import Card, Comment, PullRequest, Report, Thread
from agentflow.stages import releaser, tester
from agentflow.stages.tester import DodCheck, DodChecks

BODY = (
    "## Цель\nadd()\n\n## Definition of Done\n- [ ] add(2, 3) returns 5\n- [ ] AGENTS.md documents pytest\n\n"
    "## Контекст\n—\n"
)


def _report(src, dst, branch="feat/x", agent="developer"):
    body = b.render_report(Report(agent=agent, summary="s", branch=branch), src, dst, 0)
    return Comment(author="bot", body=body, created_at="1")


class FakeBoard:
    def __init__(self, comments=(), pr=None):
        self.thread_ = Thread(title="Add", body=BODY, author="u", comments=list(comments))
        self.pr = pr
        self.calls = []

    async def thread(self, card):
        return self.thread_

    async def move(self, card, to, report):
        self.calls.append(("move", to, report))
        back = b.BACK.get(card.status) == to
        return card.model_copy(update={"status": to, "attempts": card.attempts + back})

    async def comment(self, card, body):
        self.calls.append(("comment", body))

    async def set_field(self, card, field, value):
        self.calls.append(("set_field", field, value))

    async def pull_request(self, card, branch):
        return self.pr

    async def open_pull_request(self, card, branch, base, title, body):
        self.calls.append(("open_pr", branch, body))
        self.pr = PullRequest(id="PR", number=9, url="https://x/pull/9", state="OPEN")
        return self.pr

    async def merge_pull_request(self, pr):
        self.calls.append(("merge", pr.url))


def _card(status):
    return Card(item_id="IT", issue_id="I", number=4, repo="o/r", url="u", title="Add", status=status)


def test_dod_items_and_branch_come_from_the_card():
    assert tester.dod_items(BODY) == ["add(2, 3) returns 5", "AGENTS.md documents pytest"]
    th = Thread(title="t", body="", author="u", comments=[_report(b.DOING, b.TESTING, "agentflow/issue-4-ab")])
    assert b.branch_of(th) == "agentflow/issue-4-ab"
    assert [(r.src, r.dst) for r in b.reports(th)] == [(b.DOING, b.TESTING)]
    # a report from before the branch field: only the link names it
    old = Report(agent="developer", summary="s", artifacts=["https://github.com/o/r/tree/agentflow/issue-4-cd"])
    th.comments.append(Comment(author="bot", body=b.render_report(old, b.DOING, b.TESTING, 0), created_at="2"))
    assert b.branch_of(th) == "agentflow/issue-4-cd"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv needed for the Python gate")
@pytest.mark.parametrize("op, column", [("+", b.READY), ("-", b.DOING)])
async def test_tester_runs_its_own_test_per_item_and_sends_failures_back(repo: Path, op, column):
    (repo / "calc" / "ops.py").write_text(f"def add(a: int, b: int) -> int:\n    return a {op} b\n")

    async def checker(goal, items, gate, root):
        return DodChecks(checks=[
            # an unused import is a lint error: the tester's own file must not redden the suite
            DodCheck(item=items[0], method="test", evidence="calls add(2, 3)",
                     test_code="import os\n\nfrom calc.ops import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"),
            DodCheck(item=items[1], method="inspection", passed=True, evidence="AGENTS.md:3 `pytest`"),
        ])  # fmt: skip

    @contextlib.asynccontextmanager
    async def checkout(card, branch):
        assert branch == "feat/x"
        yield repo

    fb = FakeBoard([_report(b.DOING, b.TESTING)])
    out = await tester.process(fb, _card(b.TESTING), checker, checkout)
    assert out.column == column
    _, to, report = fb.calls[-1]
    assert to == column and report.branch == "feat/x"
    assert ("[x] add(2, 3) returns 5" in report.summary) == (op == "+")
    assert "[x] AGENTS.md documents pytest — AGENTS.md:3" in report.summary


async def test_releaser_opens_the_pr_and_waits_for_checks():
    fb = FakeBoard([_report(b.DOING, b.TESTING), _report(b.TESTING, b.READY, agent="tester")])
    out = await releaser.process(fb, _card(b.READY), may_merge=True)
    assert out.kind == "waiting"
    _, branch, body = next(c for c in fb.calls if c[0] == "open_pr")
    assert branch == "feat/x" and body.endswith("Closes #4") and "agentflow:report" not in body
    assert "### tester" not in body and "следующей стадии" not in body
    assert not any(c[0] in ("merge", "move") for c in fb.calls)  # no checks yet ≠ green


async def test_releaser_merges_green_pr_only_after_a_person_says_so():
    green = PullRequest(id="P", number=9, url="https://x/pull/9", state="OPEN", mergeable="MERGEABLE", checks="SUCCESS")
    fb = FakeBoard([_report(b.TESTING, b.READY, agent="tester")], pr=green)
    assert (await releaser.process(fb, _card(b.READY), may_merge=False)).kind == "waiting"
    assert any("«мержи»" in c[1] for c in fb.calls if c[0] == "comment")

    fb.thread_.comments.append(Comment(author="u", body="Мержи", created_at="2"))
    fb.calls.clear()
    assert (await releaser.process(fb, _card(b.READY), may_merge=False)).kind == "merged"
    assert fb.calls[0] == ("merge", green.url) and fb.calls[-1][1] == b.MERGED


async def test_red_ci_goes_back_to_the_developer_with_the_failing_checks():
    red = PullRequest(id="P", number=9, url="u9", state="OPEN", mergeable="MERGEABLE", checks="FAILURE",
                      failing=["tests https://ci/1"])  # fmt: skip
    fb = FakeBoard([_report(b.TESTING, b.READY, agent="tester")], pr=red)
    out = await releaser.process(fb, _card(b.READY), may_merge=True)
    assert out.kind == "back"
    _, to, report = fb.calls[-1]
    assert to == b.DOING and "tests https://ci/1" in report.summary and report.branch == "feat/x"


async def test_conflict_blocks():
    pr = PullRequest(id="P", number=9, url="u9", state="OPEN", mergeable="CONFLICTING", checks="SUCCESS")
    fb = FakeBoard([_report(b.TESTING, b.READY, agent="tester")], pr=pr)
    assert (await releaser.process(fb, _card(b.READY), may_merge=True)).kind == "blocked"
