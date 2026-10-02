"""Graph assembly: nodes, conditional edges, retry/timeout policies, persistence."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import get_runtime
from langgraph.store.base import BaseStore
from langgraph.types import RetryPolicy

from agentflow.config import WorkflowConfig
from agentflow.graph import nodes, routing
from agentflow.graph.context import Deps
from agentflow.graph.state import WorkflowState

# Checkpoints store our Pydantic models; allow-list them explicitly for msgpack.
SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        ("agentflow.schemas", name)
        for name in (
            "Spec",
            "Task",
            "CoderResult",
            "ReviewCycle",
            "ReviewResult",
            "PullRequest",
            "Stall",
            "GateResult",
        )
    ]
)

# Transient infra errors (rate limits, timeouts, 5xx) are retried at node level on top
# of the per-call client retries and model fallbacks. Programming errors are not.
LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=2.0, backoff_factor=3.0)


def build_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(WorkflowState, context_schema=Deps)

    g.add_node("prepare_workspace", nodes.prepare_workspace, retry_policy=LLM_RETRY)
    g.add_node("generate_spec", nodes.generate_spec, retry_policy=LLM_RETRY, timeout=600)
    g.add_node("await_spec_approval", nodes.await_spec_approval)
    g.add_node("generate_tasks", nodes.generate_tasks, retry_policy=LLM_RETRY, timeout=600)
    g.add_node("await_task_approval", nodes.await_task_approval)
    g.add_node("select_next_task", nodes.select_next_task)
    g.add_node("run_coder", nodes.run_coder, timeout=1800)  # failures are handled as attempts, not retries
    g.add_node("generate_task_test", nodes.generate_task_test, retry_policy=LLM_RETRY, timeout=600)
    g.add_node("run_review", nodes.run_review, retry_policy=LLM_RETRY, timeout=1800)
    g.add_node("adjudicate", nodes.adjudicate, timeout=600)
    g.add_node("complete_task", nodes.complete_task, retry_policy=LLM_RETRY)
    g.add_node("handle_failure", nodes.handle_failure)
    g.add_node("create_pr", nodes.create_pr, retry_policy=LLM_RETRY, timeout=1800)
    g.add_node("notify", nodes.notify)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "prepare_workspace")
    g.add_edge("prepare_workspace", "generate_spec")
    g.add_edge("generate_spec", "await_spec_approval")
    g.add_conditional_edges("await_spec_approval", routing.after_spec_approval, ["generate_tasks", "generate_spec"])
    g.add_edge("generate_tasks", "await_task_approval")
    g.add_conditional_edges("await_task_approval", routing.after_task_approval, ["select_next_task", "generate_tasks"])
    g.add_conditional_edges(
        "select_next_task", _with_config(routing.after_task_selection), ["generate_task_test", "run_coder", "create_pr"]
    )
    g.add_conditional_edges(
        "run_coder",
        _with_config(routing.after_coder),
        ["run_review", "generate_task_test", "run_coder", "handle_failure"],
    )
    g.add_conditional_edges("generate_task_test", _with_config(routing.after_test), ["run_coder", "run_review"])
    g.add_conditional_edges(
        "run_review",
        _with_config(routing.after_review),
        ["complete_task", "run_coder", "handle_failure", "adjudicate"],
    )
    g.add_conditional_edges("adjudicate", routing.after_adjudication, ["run_coder", "handle_failure"])
    g.add_edge("complete_task", "select_next_task")
    g.add_conditional_edges("handle_failure", routing.after_failure, ["select_next_task", END])
    g.add_edge("create_pr", "notify")
    g.add_edge("notify", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer, store=store, name="agentflow")


def _with_config(route):
    """Routers that need the run's WorkflowConfig read it from the runtime context."""

    def wrapped(state: WorkflowState) -> str:
        cfg: WorkflowConfig = get_runtime(Deps).context.config
        return route(state, cfg)

    wrapped.__name__ = route.__name__
    return wrapped
