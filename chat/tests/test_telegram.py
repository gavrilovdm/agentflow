import asyncio

import pytest

from agentflow_chat.config import Settings
from agentflow_chat.telegram import TelegramBridge

CHAT = 42


class FakeHttp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url, json):
        self.calls.append((url.rsplit("/", 1)[-1], json))

        class R:
            def json(self_inner):
                return {"ok": True, "result": {"message_id": 7}}

        return R()

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


class FakeSession:
    def __init__(self, chat_id, confirmer):
        self.confirmer = confirmer
        self.got: list[str] = []
        self.confirmed = None

    async def send(self, text, reply):
        self.got.append(text)
        if "запусти" in text:
            self.confirmed = await self.confirmer.confirm(CHAT, "Запустить в octo/api?")
        await reply("ok")

    async def close(self):
        pass


@pytest.fixture
def bridge():
    http = FakeHttp()
    s = Settings(telegram_bot_token="t", allowed_chat_ids=str(CHAT), confirm_timeout_s=5)
    b = TelegramBridge(s, http=http, session_factory=FakeSession)  # type: ignore[arg-type]
    return b, http


def _msg(text, chat=CHAT, reply_to=None):
    m = {"chat": {"id": chat}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"text": reply_to}
    return {"update_id": 1, "message": m}


async def _settle(b):
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.gather(*b.tasks)


async def test_unknown_chats_are_ignored(bridge):
    b, http = bridge
    await b.handle_update(_msg("привет", chat=999))
    await _settle(b)
    assert not http.calls and not b.sessions


async def test_reply_to_notification_reaches_session_with_thread(bridge):
    b, http = bridge
    await b.handle_update(_msg("убери пункт 3", reply_to="spec...\nthread: `mcp-1`"))
    await _settle(b)
    assert "mcp-1" in b.sessions[CHAT].got[0]
    assert http.sent() == ["ok"]


async def test_button_press_confirms(bridge):
    b, http = bridge
    await b.handle_update(_msg("запусти"))
    for _ in range(20):
        await asyncio.sleep(0)
    token = b.pending[CHAT][0]
    cb = {"id": "q", "data": f"c|{token}|y", "message": {"chat": {"id": CHAT}, "message_id": 7}}
    await b.handle_update({"update_id": 2, "callback_query": cb})
    await _settle(b)
    assert b.sessions[CHAT].confirmed == (True, None)
    assert CHAT not in b.pending


async def test_typed_answer_settles_open_question_instead_of_queueing(bridge):
    """The session is blocked on the question, so a queued message would deadlock until timeout."""
    b, _ = bridge
    await b.handle_update(_msg("запусти"))
    for _ in range(20):
        await asyncio.sleep(0)
    await b.handle_update(_msg("нет, в octo/web"))
    await _settle(b)
    assert b.sessions[CHAT].confirmed == (False, "нет, в octo/web")
    assert b.sessions[CHAT].got == ["запусти"]


async def test_stale_button_is_rejected(bridge):
    b, http = bridge
    cb = {"id": "q", "data": "c|dead|y", "message": {"chat": {"id": CHAT}, "message_id": 7}}
    await b.handle_update({"update_id": 2, "callback_query": cb})
    assert http.calls[-1] == ("answerCallbackQuery", {"callback_query_id": "q", "text": "Устарело"})
