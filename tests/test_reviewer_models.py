"""The reviewer's models must be able to take a forced tool call (its structured verdict)."""

from agentflow.agents import reviewer


def test_fallback_reviewer_disables_deepseek_thinking():
    # Seen live: Opus proxy hit 429, the DeepSeek fallback had thinking on, and every review
    # failed with "Thinking mode does not support this tool_choice".
    mw = reviewer._middleware("cpxopus", "deepseek-v4-pro")
    fallback = mw[-1].models[0]
    assert fallback.extra_body == {"thinking": {"type": "disabled"}}


def test_primary_deepseek_reviewer_disables_thinking():
    assert reviewer.reviewer_chat_model("deepseek-v4-flash").extra_body == {"thinking": {"type": "disabled"}}


async def test_reviewer_out_of_calls_is_forced_to_a_verdict(monkeypatch):
    """At 8 calls a live reviewer spent every one reading files and ended with no verdict
    three times, which escalated a task whose code was correct."""
    from langchain_core.messages import ToolMessage

    from agentflow.schemas import ReviewDecision, Spec, Task

    class CutOff:
        async def ainvoke(self, *a, **k):
            return {"messages": [ToolMessage(content="export const CONTACTS = []", tool_call_id="1", name="read_file")]}

    seen = {}

    class Forced:
        async def ainvoke(self, messages, config=None):
            seen["prompt"] = messages[-1].content
            return ReviewDecision(verdict="approved", summary="ok")

    monkeypatch.setattr(reviewer, "create_agent", lambda *a, **k: CutOff())
    monkeypatch.setattr(reviewer, "structured", lambda *a, **k: Forced())
    spec = Spec(
        id="s", title="T", goal="g", constraints=[], acceptance_criteria=["a", "b"],
        technical_notes="", out_of_scope=[],
    )  # fmt: skip
    task = Task.model_construct(id="t", title="t", description="d", definition_of_done="dod")
    cycle = await reviewer.review_diff(task, spec, "test", "+x", None)
    assert cycle.verdict == "approved" and not cycle.reviewer_malfunction
    assert "export const CONTACTS" in seen["prompt"]
