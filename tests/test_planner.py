import contextlib

from agentflow import board as b
from agentflow.board import Card, Comment, SubIssue, Thread
from agentflow.schemas import Spec, Task
from agentflow.stages import planner
from agentflow.stages.planner import Plan, latest_plan, render_plan


def _card(approval=None):
    return Card(item_id="IT1", issue_id="I1", number=5, repo="o/r", url="https://x/5", title="Add counter",
                status=b.PLANNING, approval=approval, owner=planner.AGENT)  # fmt: skip


def _plan(title="Counter"):
    spec = Spec(id="s", title=title, goal="a counter", constraints=[], acceptance_criteria=["a", "b -- c"],
                technical_notes="", out_of_scope=[], created_at="2026-10-28")  # fmt: skip
    tasks = [
        Task(id="task-a", title="A", description="", target_files=["a.py"], definition_of_done="a"),
        Task(id="task-b", title="B", description="", target_files=["b.py"], definition_of_done="b",
             depends_on=["task-a"]),
    ]  # fmt: skip
    return Plan(spec=spec, tasks=tasks)


class FakeBoard:
    def __init__(self, comments=(), subs=()):
        self._thread = Thread(title="Add counter", body="## Цель\nx\n<!-- agentflow:card source=mcp reply_to=- -->",
                              author="u", comments=list(comments))  # fmt: skip
        self.subs = list(subs)
        self.calls = []

    async def thread(self, card):
        return self._thread

    async def comment(self, card, body):
        self.calls.append(("comment", body))

    async def set_field(self, card, field, value):
        self.calls.append(("set_field", field, value))

    async def move(self, card, to, report):
        self.calls.append(("move", to, report))

    async def sub_issues(self, card):
        return self.subs

    async def add_sub_issue(self, card, title, body):
        s = SubIssue(issue_id=f"S{len(self.subs)}", number=10 + len(self.subs), url=f"https://x/s{len(self.subs)}",
                     title=title, body=body)  # fmt: skip
        self.subs.append(s)
        self.calls.append(("add_sub_issue", title))
        return s


def _planner(plan):
    async def run(prompt, previous, feedback, workspace):
        run.seen = (prompt, previous, feedback)
        return plan

    return run


@contextlib.asynccontextmanager
async def _checkout(card):
    yield "/nowhere"


def _posted(plan, version=1):
    return Comment(author="bot", body=render_plan(plan, version, True), created_at="1")


def test_plan_survives_the_round_trip_through_a_comment():
    plan = _plan()
    found = latest_plan(Thread(title="t", body="", author="u", comments=[_posted(plan, 2)]))
    assert found is not None and found[0] == plan and found[1] == 2  # "--" in a criterion did not end the comment


async def test_plan_is_posted_and_waits_for_a_person():
    fb = FakeBoard()
    ext = _planner(_plan())
    out = await planner.process(fb, _card(), ext, _checkout)
    assert out.kind == "awaiting"
    assert ext.seen[0].startswith("# Add counter") and "agentflow:card" not in ext.seen[0]
    body = next(c[1] for c in fb.calls if c[0] == "comment")
    assert "План v1" in body and "`task-b` B (после task-a)" in body and "«ok»" in body
    assert ("set_field", "Owner", None) in fb.calls and not any(c[0] == "move" for c in fb.calls)


async def test_no_answer_yet_means_no_new_plan():
    fb = FakeBoard([_posted(_plan())])
    assert (await planner.process(fb, _card(), _planner(_plan()), _checkout)).kind == "wait" and fb.calls == []


async def test_ok_turns_tasks_into_sub_issues_and_moves_the_card():
    fb = FakeBoard([_posted(_plan()), Comment(author="u", body="Ок!", created_at="2")])
    out = await planner.process(fb, _card(), _planner(_plan()), _checkout)
    assert out.kind == "approved"
    assert [c[1] for c in fb.calls if c[0] == "add_sub_issue"] == ["A (task-a)", "B (task-b)"]
    _, to, report = fb.calls[-1]
    assert to == b.DOING and report.artifacts == ["https://x/s0", "https://x/s1"]
    assert planner.subtask_id(fb.subs[1]) == "task-b"


async def test_sub_issues_left_by_a_dead_pass_are_not_filed_twice():
    existing = SubIssue(issue_id="S0", number=10, url="https://x/s0", title="A",
                        body=planner.render_subtask(_plan().tasks[0]))  # fmt: skip
    fb = FakeBoard([_posted(_plan()), Comment(author="u", body="ok", created_at="2")], subs=[existing])
    await planner.process(fb, _card(), _planner(_plan()), _checkout)
    assert [c[1] for c in fb.calls if c[0] == "add_sub_issue"] == ["B (task-b)"]


async def test_feedback_rewrites_the_plan_from_the_previous_one():
    fb = FakeBoard([_posted(_plan()), Comment(author="u", body="split task-a in two", created_at="2")])
    ext = _planner(_plan("Counter v2"))
    out = await planner.process(fb, _card(), ext, _checkout)
    assert out.kind == "awaiting" and out.detail == "v2"
    _, previous, feedback = ext.seen
    assert previous == _plan() and feedback == "split task-a in two"
    assert "План v2: Counter v2" in next(c[1] for c in fb.calls if c[0] == "comment")


async def test_no_approval_needed_goes_straight_to_doing():
    fb = FakeBoard()
    out = await planner.process(fb, _card(approval="none"), _planner(_plan()), _checkout)
    assert out.kind == "approved" and fb.calls[-1][1] == b.DOING
    assert "«ok»" not in next(c[1] for c in fb.calls if c[0] == "comment")


async def test_third_rejected_version_blocks():
    fb = FakeBoard([_posted(_plan(), 3), Comment(author="u", body="still wrong", created_at="2")])
    out = await planner.process(fb, _card(), _planner(_plan()), _checkout)
    assert out.kind == "blocked" and fb.calls[-1][1] == b.BLOCKED
