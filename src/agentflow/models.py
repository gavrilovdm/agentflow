"""Chat model factory with provider fallbacks and transport-level retries.

Each role (orchestrator, test generator, coder, reviewer) is configured by model
name. Structured calls get a `.with_fallbacks()` chain so an outage or rate limit on
one provider degrades to another instead of failing the run; the tool-using coder
agent gets the equivalent through `ModelFallbackMiddleware` (see agents/coder.py).
"""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from agentflow.config import get_settings

# Opus runs adaptive thinking; a non-streaming request whose max_tokens implies it
# could exceed 10 minutes is refused by the SDK. 16k is verified for both plain and
# structured (forced tool_choice) calls.
DEFAULT_MAX_TOKENS = 16_000


def chat_model(
    name: str,
    *,
    temperature: float | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    forces_tool_choice: bool = False,
) -> BaseChatModel:
    s = get_settings()
    if name.startswith("claude-"):
        kwargs: dict[str, Any] = {"model": name, "max_tokens": max_tokens, "max_retries": 3}
        if s.anthropic_api_key:  # from .env via pydantic-settings; the SDK itself only reads os.environ
            kwargs["api_key"] = s.anthropic_api_key
        if temperature is not None and "opus" not in name:  # opus thinking rejects temperature
            kwargs["temperature"] = temperature
        return ChatAnthropic(**kwargs)
    if name.startswith("deepseek-"):
        return ChatOpenAI(
            model=name,
            temperature=temperature,
            max_tokens=max_tokens,  # type: ignore[call-arg]
            api_key=s.deepseek_api_key or "unset",  # type: ignore[arg-type]
            base_url=s.deepseek_base_url,
            max_retries=3,
            # DeepSeek's thinking mode rejects a forced tool_choice, which is what
            # with_structured_output(method="function_calling") sends. Non-OpenAI params
            # must travel in extra_body: model_kwargs reach create() as kwargs and the SDK
            # raises TypeError, which took down every structured DeepSeek call.
            extra_body={"thinking": {"type": "disabled"}} if forces_tool_choice else None,
        )
    raise ValueError(f"Unknown model: {name}")


def _fallback_name(primary: str, fallback: str | None) -> str | None:
    fb = fallback if fallback is not None else get_settings().fallback_model
    return fb if fb and fb != primary else None


def structured(name: str, schema: type, *, fallback: str | None = None) -> Runnable:
    """Structured-output runnable: primary model, then fallback model on error.

    Fallbacks are attached *after* with_structured_output — a RunnableWithFallbacks
    has no with_structured_output of its own.
    """

    def make(model_name: str) -> Runnable:
        model = chat_model(model_name, forces_tool_choice=True)
        # No custom tool `name`: ChatOpenAI forwards unknown kwargs to create(), which
        # rejects them. The tool is named after the Pydantic class on every provider.
        return model.with_structured_output(schema, method="function_calling")

    primary = make(name)
    fb = _fallback_name(name, fallback)
    return primary.with_fallbacks([make(fb)]) if fb else primary


def text_model(name: str, *, fallback: str | None = None) -> Runnable:
    """Free-form text model with a fallback chain."""
    primary = chat_model(name)
    fb = _fallback_name(name, fallback)
    return primary.with_fallbacks([chat_model(fb)]) if fb else primary


def text_of(message: Any) -> str:
    """Text of an AIMessage. Opus returns a block list (thinking + text); reading only
    the string case once shipped every PR with a placeholder body."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
