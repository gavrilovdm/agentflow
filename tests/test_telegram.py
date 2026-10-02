import httpx

from agentflow.config import get_settings
from agentflow.integrations import telegram


async def test_network_errors_never_escape(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    get_settings.cache_clear()
    calls = {"n": 0}

    async def boom(self, *a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    await telegram.request_failure_decision("t-1", "dossier", ["retry", "skip"])  # must not raise
    assert calls["n"] == 2  # retried once
