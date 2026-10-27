import asyncio

from agentflow import progress, worker
from agentflow.runner import RunSnapshot
from agentflow.schemas import PullRequest, Spec, Task


def _spec():
    return Spec(
        id="s", title="Add LinkedIn", goal="g", constraints=[], acceptance_criteria=["a", "b"],
        technical_notes="", out_of_scope=[],
    )  # fmt: skip


def _task(i, status):
    return Task.model_construct(id=f"t{i}", title=f"task {i}", status=status)


def test_render_shows_phase_current_task_and_waits():
    coding = RunSnapshot(
        "r-1", "coding", None,
        {"status": "coding", "spec": _spec(), "tasks": [_task(1, "completed"), _task(2, "coding")]},
    )  # fmt: skip
    text = progress.render(coding, "run_review")
    assert text.startswith("⏳ *Reviewing…*")
    assert "✅ spec · ✅ plan · ⏳ code · ⬜ PR" in text
    assert "*Tasks* 1/2" in text and "▶️ task 2" in text

    waiting = RunSnapshot("r-1", "awaiting_approval", {"type": "tasks_approval"}, {"status": "task_review"})
    assert progress.render(waiting).startswith("⏸ *Waiting for your plan approval*")

    pr = PullRequest(number=7, url="https://github.com/o/r/pull/7", title="t", branch="b")
    done = RunSnapshot("r-1", "completed", None, {"status": "completed", "pull_request": pr})
    assert "PR #7" in progress.render(done) and pr.url in progress.render(done)

    failed = RunSnapshot("r-1", "failed", None, {"status": "failed", "tasks_approved": True, "error": "boom"})
    text = progress.render(failed)
    assert "✅ spec · ✅ plan · ❌ code" in text and "boom" in text


CLONE_CRASH = "Run crashed: GitCommandError: git clone -- https://*****:*****@github.com/o/r.git `x`"


async def test_errors_reach_telegram_verbatim(monkeypatch):
    """As Markdown, GitPython's `*****:*****` credential mask vanished into bold markers and
    the URL read https://:@github.com, which looked like an empty token."""
    shown = "```\nRun crashed: GitCommandError: git clone -- https://*****:*****@github.com/o/r.git 'x'\n```"
    failed = RunSnapshot("r-1", "failed", None, {"status": "failed", "error": CLONE_CRASH})
    assert shown in progress.render(failed)

    sent = []

    async def send(text, buttons=None, *, silent=False):
        sent.append(text)

    monkeypatch.setattr(worker.telegram, "send_message", send)
    await worker._after(failed)
    assert len(sent) == 1 and shown in sent[0]


async def test_status_card_edits_one_message_throttled_and_never_raises(monkeypatch):
    calls = []

    async def send(text, buttons=None, *, silent=False):
        calls.append(("send", text, silent))
        return 11

    async def edit(message_id, text):
        calls.append(("edit", text, message_id))
        if text == "explode":
            raise RuntimeError("telegram down")

    monkeypatch.setattr(progress.telegram, "send_message", send)
    monkeypatch.setattr(progress.telegram, "edit_message", edit)
    card = progress.StatusCard("r-1")
    await card.show("a")
    await card.show("b")  # within the interval: dropped
    await card.show("c", force=True)
    await card.show("explode", force=True)  # must not fail the run
    assert calls == [("send", "a", True), ("edit", "c", 11), ("edit", "explode", 11)]


async def test_runner_reports_steps_and_a_broken_status_never_crashes_the_run(tmp_path, monkeypatch):
    from agentflow import worker
    from agentflow.agents import orchestrator
    from agentflow.config import WorkflowConfig
    from agentflow.runner import open_persistence, start_run

    async def fake_spec(prompt, previous=None, feedback=None, *, workspace=None):
        return _spec()

    monkeypatch.setattr(orchestrator, "generate_spec", fake_spec)
    shown = []

    async def send(text, buttons=None, *, silent=False):
        shown.append(text)
        return 1

    monkeypatch.setattr(progress.telegram, "send_message", send)
    async with open_persistence(in_memory=True) as p:
        steps: list[str] = []

        async def on_step(node):
            steps.append(node)

        cfg = WorkflowConfig(local_path=str(tmp_path))
        await start_run(p, "p", cfg, thread_id="r-steps", on_step=on_step)
        assert steps[:2] == ["prepare_workspace", "generate_spec"]

        async def broken_snapshot(graph, thread_id):
            raise RuntimeError("db hiccup")

        monkeypatch.setattr(worker, "snapshot", broken_snapshot)
        card, step = await worker._card({"persistence": p}, "r-steps", "⏳ *Picked up*")
        card._last = 0.0  # make the next edit due
        await step("generate_tasks")  # logged, not raised (a raise would mark the run crashed)
        assert shown == ["⏳ *Picked up*"]


class FakeRedis:
    def __init__(self):
        self.keys: dict[str, int | None] = {}

    async def set(self, key, value, ex=None):
        self.keys[key] = ex

    async def delete(self, key):
        self.keys.pop(key, None)

    async def scan_iter(self, match):
        for k in list(self.keys):
            if k.startswith(match.rstrip("*")):
                yield k.encode()


async def test_heartbeat_marks_a_thread_running_only_while_its_job_runs():
    """A worker killed outright records nothing, so a run is known to be alive only by a
    heartbeat that expires on its own."""
    redis = FakeRedis()
    async with worker._heartbeat({"redis": redis}, "t-9"):
        await asyncio.sleep(0)
        assert await progress.running_threads(redis) == {"t-9"}
        assert redis.keys[progress.RUNNING_PREFIX + "t-9"] == progress.HEARTBEAT_TTL_S
    assert await progress.running_threads(redis) == set()
    assert await progress.running_threads(object()) is None  # unknown, not "nothing is running"


def test_render_shows_a_stalled_run_and_its_cause():
    snap = RunSnapshot("r-3", "coding", None, {"status": "coding", "spec": _spec()}, stalled=True)
    snap.node_errors = ["run_coder: CancelledError()"]
    text = progress.render(snap)
    assert text.startswith("🧟 *Stalled*") and "run_coder: CancelledError()" in text
