"""Live smoke test: one structured call per configured model, each provider in isolation.

    uv run pytest -m live

Two provider-specific bugs (DeepSeek params in model_kwargs, a tool `name` kwarg that
ChatOpenAI forwards to create()) passed every offline test and only showed up when a
fallback was actually exercised. Fallback chains hide a broken fallback until the day
the primary goes down, so each model is tested on its own.
"""

import pytest

from agentflow.config import get_settings
from agentflow.models import structured
from agentflow.schemas import FailureRuling

# One loop for the module: langchain-openai caches its async client across calls.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


def _models() -> list[str]:
    s = get_settings()
    return sorted({s.orchestrator_model, s.reviewer_model, s.coder_model, s.fallback_model} - {""})


@pytest.mark.parametrize("model", _models())
async def test_structured_call(model: str):
    s = get_settings()
    if model.startswith("claude-") and not s.anthropic_api_key:
        pytest.skip("no ANTHROPIC_API_KEY")
    if model.startswith("deepseek-") and not s.deepseek_api_key:
        pytest.skip("no DEEPSEEK_API_KEY")
    ruling = await structured(model, FailureRuling, fallback="").ainvoke(
        "A test asserts add(1, 2) == 4 and the code returns 3. Which artifact is wrong?"
    )
    assert ruling.culprit == "test"


@pytest.mark.parametrize("model", _models())
async def test_reviewer_agent_structured_verdict(model: str):
    """The reviewer is an agent whose last step is a forced tool call (ToolStrategy); the
    plain structured-output test above did not exercise that path, and it broke for DeepSeek."""
    from langchain.agents import create_agent

    from agentflow.agents.reviewer import reviewer_chat_model

    s = get_settings()
    if model.startswith("claude-") and not s.anthropic_api_key:
        pytest.skip("no ANTHROPIC_API_KEY")
    if model.startswith("deepseek-") and not s.deepseek_api_key:
        pytest.skip("no DEEPSEEK_API_KEY")
    agent = create_agent(reviewer_chat_model(model), tools=[], response_format=FailureRuling)
    out = await agent.ainvoke(
        {
            "messages": [
                {"role": "user", "content": "A test asserts add(1, 2) == 4 and the code returns 3. Which is wrong?"}
            ]
        }
    )
    assert out["structured_response"].culprit == "test"
