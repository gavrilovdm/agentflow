"""cpx* models go through cursor-proxy (>= 0.2.0), which absorbs the upstream quirks itself."""

from langchain_openai import ChatOpenAI

from agentflow.config import Settings
from agentflow.models import chat_model


def test_claude_roles_default_to_cursor_proxy():
    s = Settings(_env_file=None)
    assert (s.orchestrator_model, s.test_generator_model, s.reviewer_model) == ("cpxopus",) * 3


def test_cpx_model_is_a_plain_openai_client():
    model = chat_model("cpxopus")
    assert type(model) is ChatOpenAI
    assert model.openai_api_base == Settings(_env_file=None).claude_proxy_url
