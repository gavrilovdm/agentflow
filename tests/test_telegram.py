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


class _FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def _fake_client(responses, calls):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            method = url.rsplit("/", 1)[-1]
            calls.append(method)
            if method == "deleteWebhook":
                return _FakeResp(200, {"ok": True})
            return responses.pop(0)

    return Client


async def test_poll_hands_updates_over_and_stops_on_bad_token(monkeypatch):
    """Locally there is no public URL for the webhook, so button presses only arrive by polling."""
    calls, seen = [], []
    responses = [
        _FakeResp(200, {"ok": True, "result": [{"update_id": 7, "callback_query": {"data": "approve|t-1"}}]}),
        _FakeResp(401, {"ok": False, "error_code": 401}),
    ]
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _fake_client(responses, calls))

    async def handle(u):
        seen.append(u["update_id"])

    await telegram.poll(handle)  # returns instead of retrying a rejected token forever
    assert seen == [7]
    assert calls == ["deleteWebhook", "getUpdates", "getUpdates"]


async def test_poll_pauses_after_an_error(monkeypatch):
    calls, sleeps = [], []
    responses = [_FakeResp(502, {"ok": False}), _FakeResp(401, {"ok": False})]
    monkeypatch.setattr(telegram.httpx, "AsyncClient", _fake_client(responses, calls))

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)

    async def handle(u):
        pass

    await telegram.poll(handle)
    assert sleeps == [telegram.POLL_RETRY_S]
