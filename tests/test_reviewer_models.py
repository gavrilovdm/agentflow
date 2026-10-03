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
