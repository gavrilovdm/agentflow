import pytest

from agentflow.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Tests never talk to real providers, Telegram or GitHub."""
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "VOYAGE_API_KEY",
        "API_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "TELEGRAM_WEBHOOK_SECRET",
    ):
        monkeypatch.setenv(var, "")
    # Most tests exercise the flow, not auth; test_api.py turns this off to check fail-closed.
    monkeypatch.setenv("INSECURE_DEV", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
