from langgraph.graph import END

from agentflow.config import WorkflowConfig
from agentflow.graph import routing
from agentflow.schemas import CoderResult, ReviewResult, Stall, Task

CFG = WorkflowConfig(max_review_cycles=2, max_gate_failures=3, max_coder_fix_attempts=2)


def t(id_, deps=(), status="pending", **kw) -> Task:
    return Task(
        id=id_,
        title=id_,
        description="",
        target_files=[],
        definition_of_done="",
        depends_on=list(deps),
        status=status,
        **kw,
    )


def state(task: Task, **kw):
    return {"current_task_id": task.id, "tasks": [task], **kw}


def test_review_approved_completes():
    s = state(t("a"), review_results={"a": ReviewResult(task_id="a", approved=True)})
    assert routing.after_review(s, CFG) == "complete_task"


def test_review_budgets_are_separate():
    assert routing.after_review(state(t("a", gate_failures=2)), CFG) == "run_coder"
    assert routing.after_review(state(t("a", gate_failures=3)), CFG) == "handle_failure"
    assert routing.after_review(state(t("a", review_cycles=2)), CFG) == "handle_failure"


def test_stall_goes_to_referee_once():
    stalled = {"a": Stall(signature="x", repeats=2)}
    assert routing.after_review(state(t("a"), stalls=stalled), CFG) == "adjudicate"
    assert routing.after_review(state(t("a"), stalls=stalled, adjudicated={"a": True}), CFG) == "handle_failure"


def test_adjudication_outcome():
    assert routing.after_adjudication(state(t("a"), stalls={"a": Stall()})) == "run_coder"
    assert routing.after_adjudication(state(t("a"), stalls={"a": Stall(signature="x", repeats=2)})) == "handle_failure"


def test_coder_attempt_budget():
    assert routing.after_coder(state(t("a", coder_fix_attempts=1)), CFG) == "run_coder"
    assert routing.after_coder(state(t("a", coder_fix_attempts=2)), CFG) == "handle_failure"


def test_dependents_are_transitive_and_skip_closed_tasks():
    tasks = [t("a"), t("b", ["a"]), t("c", ["b"]), t("d"), t("e", ["a"], status="completed")]
    assert routing.dependents_of("a", tasks) == ["b", "c"]


def test_next_runnable_respects_dependencies():
    tasks = [t("a", status="completed"), t("b", ["c"]), t("c", ["a"])]
    assert routing.next_runnable(tasks).id == "c"


def test_test_first_order():
    first, after = CFG, CFG.model_copy(update={"test_strategy": "test_after"})
    s = state(t("a"))
    assert routing.after_task_selection(s, first) == "generate_task_test"
    assert routing.after_task_selection(s, after) == "run_coder"
    assert routing.after_task_selection({"current_task_id": None}, first) == "create_pr"
    assert routing.after_test(s, first) == "run_coder"
    assert routing.after_test(s, after) == "run_review"
    ok = {"task_results": {"a": CoderResult(task_id="a", success=True)}}
    assert routing.after_coder({**s, **ok}, first) == "run_review"
    assert routing.after_coder({**s, **ok}, after) == "generate_task_test"


def test_after_failure():
    assert routing.after_failure({"status": "failed"}) == END
    assert routing.after_failure({"status": "coding"}) == "select_next_task"


def test_plan_validation_breaks_cycles_and_dangling_deps():
    from agentflow.agents.orchestrator import validate_plan

    tasks = validate_plan([t("a", ["b"]), t("b", ["a"]), t("c", ["zzz", "a"])])
    assert routing.next_runnable(tasks) is not None
    assert tasks[2].depends_on == ["a"]


def test_reviewer_malfunctions_are_capped():
    assert routing.after_review(state(t("a", reviewer_malfunctions=2)), CFG) == "run_coder"
    assert routing.after_review(state(t("a", reviewer_malfunctions=3)), CFG) == "handle_failure"


def test_stall_catches_oscillation_not_just_streaks():
    # Seen live: test fails (A) → coder changes an API → reviewer rejects (B) → revert → A …
    s = None
    for sig in ["A", "B", "A", "B"]:
        s = routing.next_stall(s, sig, approved=False)
    assert s.repeats == 1
    s = routing.next_stall(s, "A", approved=False)
    assert s.repeats == routing.STALL_REPEATS  # third A → referee
    assert routing.after_review(state(t("a"), stalls={"a": s}), CFG) == "adjudicate"
    assert routing.next_stall(s, "A", approved=True) == Stall()


def test_stall_streak_still_detected():
    s = None
    for sig in ["A", "A", "A"]:
        s = routing.next_stall(s, sig, approved=False)
    assert s.repeats == 2
