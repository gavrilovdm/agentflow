"""cpx* models go through cursor-proxy, which silently drops what it doesn't forward."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentflow.config import Settings
from agentflow.models import CursorProxyChat, chat_model


def _payload(messages, **kwargs):
    model = chat_model("cpxopus", temperature=0.3)
    assert isinstance(model, CursorProxyChat)
    return model._get_request_payload(messages, **kwargs)


def test_claude_roles_default_to_cursor_proxy():
    s = Settings(_env_file=None)
    assert (s.orchestrator_model, s.test_generator_model, s.reviewer_model) == ("cpxopus",) * 3


def test_system_text_moves_into_first_user_message():
    # The proxy drops `system`: the orchestrator's rules would be ignored without an error.
    payload = _payload([SystemMessage("RULES"), HumanMessage("request")])
    assert [m["role"] for m in payload["messages"]] == ["user"]
    assert payload["messages"][0]["content"] == "RULES\n\nrequest"


def test_no_temperature_is_sent():
    # Forwarded as is, and Opus answers it with 400.
    assert "temperature" not in _payload([HumanMessage("hi")], temperature=0.5)


def test_structured_call_forces_the_tool():
    from agentflow.schemas import SpecDraft

    model = chat_model("cpxopus", forces_tool_choice=True)
    bound = model.with_structured_output(SpecDraft, method="function_calling")
    first = bound.first  # type: ignore[attr-defined]
    payload = model._get_request_payload([SystemMessage("RULES"), HumanMessage("x")], **first.kwargs)
    assert payload["tool_choice"]["function"]["name"] == "SpecDraft"
    assert all(m["role"] != "system" for m in payload["messages"])


def test_empty_text_next_to_tool_calls_is_dropped():
    # Becomes an empty text block → Anthropic 400 "text content blocks must be non-empty".
    call = {"name": "f", "args": {}, "id": "c1"}
    payload = _payload([HumanMessage("go"), AIMessage("", tool_calls=[call]), ToolMessage("ok", tool_call_id="c1")])
    assistant = payload["messages"][1]
    assert "content" not in assistant and assistant["tool_calls"]
