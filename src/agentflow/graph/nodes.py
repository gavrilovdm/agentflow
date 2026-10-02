"""Graph nodes: spec → plan → (code → test → gate → review)* → PR.

Ported from the TypeScript pipeline with its hard-won edge cases intact; comments
explain the failure each rule exists to prevent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agentflow.agents import coder, orchestrator, reviewer, test_generator
from agentflow.config import get_settings
from agentflow.gate import Gate, detect_gate
from agentflow.graph.context import Deps
from agentflow.graph.routing import dependents_of, next_runnable, next_stall
from agentflow.graph.state import WorkflowState, replace_task, task_by_id
from agentflow.integrations import github, telegram
from agentflow.integrations import workspace as ws
from agentflow.memory.lessons import recall_lessons, remember_review_feedback
from agentflow.models import text_model, text_of
from agentflow.rag.indexer import index_repo
from agentflow.rag.retriever import pack_context
from agentflow.schemas import ApprovalDecision, CoderResult, ReviewCycle, ReviewResult, Stall

log = logging.getLogger("agentflow.graph")


def progress(runtime: Runtime[Deps], message: str, **data: object) -> None:
    """Log and stream a progress event (visible to API clients via stream_mode="custom")."""
    log.info(message, extra={"data": data})
    with contextlib.suppress(Exception):  # no stream consumer attached
        runtime.stream_writer({"message": message, **data})


def _ws(state: WorkflowState) -> Path:
    return Path(state["workspace"])


def _gate(state: WorkflowState) -> Gate:
    hints = [f for t in state.get("tasks", []) for f in t.target_files]
    return detect_gate(_ws(state), hints)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


async def _repo_context(runtime: Runtime[Deps], state: WorkflowState, query: str, budget: int) -> str:
    retriever = runtime.context.retriever(state["repo_id"])
    code = await retriever.search(query, k=10, kind="code")
    docs = await retriever.search(query, k=4, kind="doc")
    return pack_context(docs + code, budget_tokens=budget)


# ─── Setup ───────────────────────────────────────────────────────────────────


async def prepare_workspace(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    cfg = runtime.context.config
    branch = f"agentflow/{int(time.time())}"
    path = await asyncio.to_thread(ws.prepare_workspace, cfg.target_repo, branch, cfg.local_path)
    repo_id = ws.repo_id(cfg.target_repo, path)
    stats = await index_repo(path, repo_id, runtime.context.chunk_store, runtime.context.embeddings)
    progress(runtime, f"workspace ready, indexed {stats.files_seen} files", repo=repo_id, **vars(stats))
    return {
        "workspace": str(path),
        "branch": branch,
        "repo_id": repo_id,
        "index_stats": vars(stats),
        "status": "speccing",
    }


# ─── Spec & plan (human-in-the-loop) ─────────────────────────────────────────


async def generate_spec(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    context = await _repo_context(runtime, state, state["user_prompt"], budget=6000)
    feedback = state.get("spec_feedback")
    spec = await orchestrator.generate_spec(
        state["user_prompt"], context, previous=state.get("spec") if feedback else None, feedback=feedback
    )
    progress(runtime, f"spec v{spec.version}: {spec.title}")
    return {"spec": spec, "status": "spec_review"}


def await_spec_approval(state: WorkflowState) -> dict:
    spec = state["spec"]
    assert spec
    raw = interrupt({"type": "spec_approval", "spec": spec.model_dump()})
    decision = ApprovalDecision.model_validate(raw)
    if not decision.approved:
        return {"spec_approved": False, "spec_feedback": decision.feedback or "Rejected without comment."}
    return {"spec_approved": True, "spec_feedback": None, "status": "planning"}


async def generate_tasks(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    spec = state["spec"]
    assert spec
    context = await _repo_context(runtime, state, f"{spec.goal}\n{spec.technical_notes}", budget=5000)
    feedback = state.get("tasks_feedback")
    tasks = await orchestrator.generate_tasks(
        spec, context, previous=state.get("tasks") if feedback else None, feedback=feedback
    )
    progress(runtime, f"planned {len(tasks)} tasks", tasks=[t.id for t in tasks])
    return {"tasks": tasks, "status": "task_review"}


def await_task_approval(state: WorkflowState) -> dict:
    raw = interrupt({"type": "tasks_approval", "tasks": [t.model_dump() for t in state["tasks"]]})
    decision = ApprovalDecision.model_validate(raw)
    if not decision.approved:
        return {"tasks_approved": False, "tasks_feedback": decision.feedback or "Rejected without comment."}
    return {"tasks_approved": True, "tasks_feedback": None, "status": "coding"}


# ─── Task loop ───────────────────────────────────────────────────────────────


def select_next_task(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    tasks = state["tasks"]
    nxt = next_runnable(tasks)
    if nxt is None:
        return {"current_task_id": None}
    done = sum(t.status == "completed" for t in tasks)
    progress(runtime, f"[{done + 1}/{len(tasks)}] {nxt.id}: {nxt.title}", task=nxt.id)
    return {"current_task_id": nxt.id, "tasks": replace_task(tasks, nxt.id, status="coding")}


async def run_coder(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    task_id = state["current_task_id"]
    assert task_id
    task, spec, root = task_by_id(state, task_id), state["spec"], _ws(state)
    assert spec

    query = f"{task.title}\n{task.description}\nFiles: {' '.join(task.target_files)}"
    retrieved = await _repo_context(runtime, state, query, budget=6000)
    lessons = await recall_lessons(runtime.store, state["repo_id"], query)
    test_path = state.get("tests", {}).get(task_id)
    ctx = coder.CoderContext(
        task=task,
        spec=spec,
        workspace=root,
        language=_gate(state).language,
        test_content=_read(root / test_path) if test_path else "",
        retrieved_context=retrieved,
        lessons=lessons,
    )

    # Two failure sources feed a retry: the coder itself erroring out, and the coder
    # succeeding while gate/reviewer rejected the output. Without the latter the coder
    # restarted from a blank slate and re-emitted the same rejected code every cycle.
    prior = state.get("task_results", {}).get(task_id)
    prior_review = state.get("review_results", {}).get(task_id)
    feedback = None
    if prior and not prior.success and prior.error:
        feedback = prior.error
    elif prior_review and not prior_review.approved and prior_review.cycles:
        last = prior_review.cycles[-1]
        feedback = "\n".join([last.comments, *last.change_requests]) or "The previous attempt was rejected."

    result = await coder.run_coder(ctx, runtime.context.retriever(state["repo_id"]), feedback)
    # Carry forward everything written for this task: a fix attempt rewrites one or two
    # files, and replacing the set shrank the review diff until the reviewer rejected
    # the task for "missing" files that were on disk all along.
    merged = CoderResult(
        task_id=task_id,
        success=result.success,
        error=result.error,
        written_files={**(prior.written_files if prior else {}), **result.written_files},
    )
    update: dict = {"task_results": {task_id: merged}}
    if not result.success:
        update["tasks"] = replace_task(state["tasks"], task_id, coder_fix_attempts=task.coder_fix_attempts + 1)
    progress(runtime, f"coder {'wrote' if result.success else 'failed'}: {list(result.written_files)}", task=task_id)
    return update


async def generate_task_test(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    """Write the task's acceptance test.

    test_first (default): before any code, from the task's interface and Definition of Done.
    The test must then fail (red): a test that passes before the code exists asserts nothing
    the task adds, so it is regenerated once with that feedback.
    test_after: after the code, using it only for names and imports.
    """
    task_id = state["current_task_id"]
    assert task_id
    if state.get("tests", {}).get(task_id):
        return {}  # keep the target stable across the coder→gate→review retry loop
    task, spec, root, gate = task_by_id(state, task_id), state["spec"], _ws(state), _gate(state)
    assert spec
    test_first = runtime.context.config.test_strategy == "test_first"
    result = state.get("task_results", {}).get(task_id)
    written = {} if test_first or result is None else result.written_files

    # Before the code exists the generator must still see the code the task modifies: a
    # test-first draft once asserted that an untouched insert() returns a dict instead of
    # its id, and coder and reviewer then ping-ponged over it until the budget ran out.
    existing = {p: _read(root / p) for p in task.target_files if (root / p).is_file()} if test_first else None
    rel = await test_generator.generate_test(task, spec, gate, root, written, existing_files=existing)
    if test_first and await _passes(gate, root, rel):
        progress(runtime, f"{rel} passes before any code exists — regenerating", task=task_id)
        rel = await test_generator.generate_test(
            task,
            spec,
            gate,
            root,
            written,
            previous_test=_read(root / rel),
            ruling="This test already passes although the task is not implemented yet, so it verifies nothing "
            "the task adds. Assert the new behaviour from the Definition of Done.",
            existing_files=existing,
        )
        if await _passes(gate, root, rel):
            # Can be legitimate (e.g. a pure refactor); keep it rather than loop.
            progress(runtime, f"{rel} still green before implementation — keeping it", task=task_id)
    progress(runtime, f"acceptance test written: {rel}", task=task_id, red=test_first)
    return {"tests": {task_id: rel}}


async def _passes(gate: Gate, root: Path, test_rel: str) -> bool:
    """Red check: run only this test; lint is irrelevant before code exists."""
    return (await gate.run(root, [test_rel], enable_lint=False)).passed


async def run_review(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    cfg = runtime.context.config
    task_id = state["current_task_id"]
    assert task_id
    task, spec, root = task_by_id(state, task_id), state["spec"], _ws(state)
    assert spec
    tests = state.get("tests", {})
    own_test = tests.get(task_id)
    # Re-run the tests of already-completed tasks: this is what catches a later task
    # quietly breaking an earlier one while it is still the change under review.
    regression = [tests[t.id] for t in state["tasks"] if t.status == "completed" and t.id in tests]
    test_paths = [own_test, *regression] if own_test else None

    written = list(state["task_results"][task_id].written_files)
    gate = await _gate(state).run(root, test_paths, cfg.enable_lint, lint_paths=written)
    if not gate.passed:
        cycle = ReviewCycle(verdict="failed", comments=gate.summary, change_requests=[gate.summary])
    else:
        diff = await asyncio.to_thread(ws.stage_and_diff, root, written)
        cycle = await reviewer.review_diff(
            task, spec, _read(root / own_test) if own_test else "", diff, runtime.context.retriever(state["repo_id"])
        )

    approved = cycle.verdict == "approved"
    gate_failed = cycle.verdict == "failed"
    rejected = not approved and not gate_failed and not cycle.reviewer_malfunction
    tasks = replace_task(
        state["tasks"],
        task_id,
        gate_failures=task.gate_failures + int(gate_failed),
        review_cycles=task.review_cycles + int(rejected),
        reviewer_malfunctions=task.reviewer_malfunctions + int(cycle.reviewer_malfunction),
    )

    # An attempt failing exactly like the previous one is not converging; track it so
    # the budget isn't spent on a loop that has already shown it repeats itself.
    signature = " ".join(cycle.comments.split())[:300]
    stall = next_stall(state.get("stalls", {}).get(task_id), signature, approved)
    repeats = stall.repeats

    budget = (
        f"gate {task.gate_failures + 1}/{cfg.max_gate_failures}"
        if gate_failed
        else f"review {task.review_cycles + int(rejected)}/{cfg.max_review_cycles}"
    )
    progress(
        runtime,
        f"{task_id}: {cycle.verdict} ({budget}{f', repeat x{repeats + 1}' if repeats else ''}) "
        f"— {cycle.comments.splitlines()[0][:160] if cycle.comments else ''}",
        task=task_id,
        verdict=cycle.verdict,
    )
    return {
        "review_results": {task_id: ReviewResult(task_id=task_id, approved=approved, cycles=[cycle])},
        "review_history": {task_id: [cycle]},
        "tasks": tasks,
        "stalls": {task_id: stall},
    }


async def adjudicate(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    """Called once per task when the same failure keeps repeating: is the test or the
    code at fault? A test contradicting its own task is unsatisfiable, so every
    remaining attempt would be wasted. If the test is wrong, regenerate it with the
    ruling as feedback and give the coder a fresh run."""
    task_id = state["current_task_id"]
    assert task_id
    task, spec, root = task_by_id(state, task_id), state["spec"], _ws(state)
    assert spec
    test_rel = state["tests"][task_id]
    test_content = _read(root / test_rel)
    try:
        # Every distinct recent failure, so an oscillation (test fails ↔ reviewer rejects the
        # change that made it pass) is visible to the referee as the contradiction it is.
        recent = list(dict.fromkeys(state["stalls"][task_id].history))
        ruling = await reviewer.adjudicate(task, spec, test_content, "\n\n---\n\n".join(recent))
    except Exception as exc:  # noqa: BLE001 — referee unavailable: the failure stands
        progress(runtime, f"referee unavailable: {exc}", task=task_id)
        return {"adjudicated": {task_id: True}}

    if ruling.culprit == "code":
        progress(runtime, f"{task_id}: referee says the code is at fault", task=task_id)
        return {"adjudicated": {task_id: True}}  # stall left intact → handle_failure

    progress(runtime, f"{task_id}: referee says the test is at fault — regenerating", task=task_id)
    written = state["task_results"][task_id].written_files
    rel = await test_generator.generate_test(
        task, spec, _gate(state), root, written, previous_test=test_content, ruling=ruling.reasoning
    )
    return {
        "adjudicated": {task_id: True},
        "tests": {task_id: rel},
        "stalls": {task_id: Stall()},
        "review_results": {task_id: ReviewResult(task_id=task_id, approved=False)},
    }


async def complete_task(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    task_id = state["current_task_id"]
    assert task_id
    task, root = task_by_id(state, task_id), _ws(state)
    # The acceptance test belongs in the same commit as the code: otherwise the PR
    # ships code with no tests and workspace cleanup deletes them unrecoverably.
    paths = [*state["task_results"][task_id].written_files, state.get("tests", {}).get(task_id, "")]
    await asyncio.to_thread(ws.commit, root, [p for p in paths if p], f"feat({task_id}): {task.title}")

    # Re-index so the next task can retrieve what this one just wrote.
    await index_repo(root, state["repo_id"], runtime.context.chunk_store, runtime.context.embeddings)
    # Review feedback that was followed by an approved fix is validated knowledge.
    if runtime.store is not None:
        saved = await remember_review_feedback(
            runtime.store, state["repo_id"], task, state.get("review_history", {}).get(task_id, [])
        )
        if saved:
            progress(runtime, f"stored {saved} lesson(s) from review feedback", task=task_id)
    return {"tasks": replace_task(state["tasks"], task_id, status="completed")}


async def handle_failure(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    task_id = state.get("current_task_id")
    tasks = state["tasks"]
    task = task_by_id(state, task_id) if task_id else None
    coder_result = state.get("task_results", {}).get(task_id or "")
    review = state.get("review_results", {}).get(task_id or "")
    last = review.cycles[-1] if review and review.cycles else None
    stall = state.get("stalls", {}).get(task_id or "")

    if coder_result and not coder_result.success:
        reason = f"coder failed after {task.coder_fix_attempts if task else 0} attempts: {coder_result.error}"
    elif last and task:
        # Report the running totals across every pass, not just the last pass's cycles.
        reason = (
            f"{last.verdict} after {task.gate_failures} gate failure(s), {task.review_cycles} review cycle(s) "
            f"and {task.reviewer_malfunctions} reviewer malfunction(s)"
            + (" — stalled, repeating the same failure" if stall and stall.repeats >= 2 else "")
            + f": {last.comments}"
        )
    else:
        reason = state.get("error") or "unknown"

    # Skip only the subtree that needed this task. Aborting the whole run threw away
    # every completed, reviewed task; unrelated work is worth finishing.
    blocked = dependents_of(task_id, tasks) if task_id else []
    failed = {task_id: reason} if task_id else {}
    failed |= {b: f'Skipped — depends on failed task "{task_id}"' for b in blocked}
    casualties = {task_id, *blocked}
    tasks = [t.model_copy(update={"status": "failed"}) if t.id in casualties else t for t in tasks]
    progress(runtime, f"task {task_id} failed: {reason[:300]}", task=task_id, skipped=blocked)

    if not any(t.status == "completed" for t in tasks) and not any(t.status == "pending" for t in tasks):
        # Nothing salvageable → no PR. The workspace is kept for diagnosis.
        spec = state.get("spec")
        title = spec.title if spec else "?"
        await telegram.send_message(f"❌ *Workflow failed*\n\n*Feature:* {title}\n*Reason:* {reason[:1500]}")
        return {"status": "failed", "error": reason, "tasks": tasks, "failed_tasks": failed}
    return {"tasks": tasks, "failed_tasks": failed, "current_task_id": None}


# ─── Delivery ────────────────────────────────────────────────────────────────


async def create_pr(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    cfg = runtime.context.config
    if cfg.target_repo is None:
        progress(runtime, "in-place mode: commits made locally, no PR")
        return {"status": "notifying"}
    root, spec = _ws(state), state["spec"]
    assert spec

    # The per-task gate only runs that task's tests; an unconditional full-suite run
    # lets the PR state plainly whether everything builds and passes together.
    final = await _gate(state).run(root, None, cfg.enable_lint)
    verification = (
        "✅ Full typecheck and test suite pass."
        if final.passed
        else f"⚠️ Final verification did not fully pass:\n\n```\n{final.summary[:1500]}\n```"
    )
    await asyncio.to_thread(ws.push, root, state["branch"])

    done = [t.title for t in state["tasks"] if t.status == "completed"]
    description = text_of(
        await text_model(get_settings().orchestrator_model).ainvoke(
            f"Write a concise GitHub PR description.\nTitle: {spec.title}\nGoal: {spec.goal}\n"
            f"Tasks completed: {', '.join(done)}",
            config={"tags": ["pr-description"]},
        )
    )
    failed = state.get("failed_tasks", {})
    incomplete = (
        f"\n\n---\n\n### ⚠️ Incomplete tasks ({len(failed)})\n\n"
        + "\n".join(f"- **{k}** — {v.splitlines()[0]}" for k, v in failed.items())
        if failed
        else ""
    )
    issue = state.get("issue_number")
    closes = f"\n\nCloses #{issue}" if issue else ""
    body = f"{description}{incomplete}\n\n---\n\n### Verification\n\n{verification}{closes}"
    pr = await github.open_pull_request(cfg.target_repo, state["branch"], spec.title, body)
    if issue:
        await github.comment_on_issue(cfg.target_repo, issue, f"🤖 Opened {pr.url}")
    progress(runtime, f"opened PR #{pr.number}", url=pr.url)
    return {"pull_request": pr, "status": "notifying"}


async def notify(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    spec, pr, failed = state["spec"], state.get("pull_request"), state.get("failed_tasks", {})
    lines = [f"✅ *Workflow completed*\n\n*Feature:* {spec.title if spec else '?'}"]
    if failed:
        lines.append(f"*Incomplete:* {', '.join(failed)}")
    if pr:
        lines.append(f"*Pull request:* {pr.url}")
    try:
        await telegram.send_message("\n".join(lines))
    except Exception as exc:  # noqa: BLE001 — a notification must never fail the run
        log.warning("notification failed: %s", exc)
    return {}


def finalize(state: WorkflowState, runtime: Runtime[Deps]) -> dict:
    ws.cleanup_workspace(_ws(state), runtime.context.config.target_repo)
    return {"status": "completed"}
