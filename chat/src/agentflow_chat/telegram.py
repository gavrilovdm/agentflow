"""Telegram front end: long polling, one ChatSession per chat, inline buttons for confirmations.

This bot is separate from agentflow's: that one owns its webhook (approval buttons) and keeps
sending run notifications directly, so they arrive even when this layer is down.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from typing import Any

import httpx

from agentflow_chat.agent import ChatSession, Confirmer
from agentflow_chat.config import Settings, get_settings
from agentflow_chat.policy import is_yes, with_reply_context

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
HELP = (
    "Пишите задачу словами: «добавь в мобильный бэкенд эндпоинт X» или «найди репозиторий на Python "
    "с fastapi и зелёным CI». Перед запуском я уточню репозиторий. Ответьте реплаем на уведомление "
    "agentflow, чтобы поправить спеку или план. /new начинает разговор заново."
)


class TelegramBridge(Confirmer):
    def __init__(
        self,
        settings: Settings | None = None,
        http: httpx.AsyncClient | None = None,
        session_factory: Callable[[int, Confirmer], ChatSession] | None = None,
    ):
        self.s = settings or get_settings()
        self.http = http or httpx.AsyncClient(timeout=70)
        self.make_session = session_factory or (lambda chat_id, confirmer: ChatSession(chat_id, confirmer, self.s))
        self.sessions: dict[int, ChatSession] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        # chat_id -> (token, future): at most one open question per chat.
        self.pending: dict[int, tuple[str, asyncio.Future[tuple[bool, str | None]]]] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def call(self, method: str, payload: dict[str, Any]) -> Any:
        r = await self.http.post(API.format(token=self.s.telegram_bot_token, method=method), json=payload)
        body = r.json()
        if not body.get("ok"):
            log.warning("telegram %s failed: %s", method, str(body)[:300])
        return body.get("result")

    async def send(self, chat_id: int, text: str, buttons: list[list[dict[str, str]]] | None = None) -> Any:
        # Plain text: model output has stray * and _ that Telegram rejects as bad Markdown.
        result = None
        for i in range(0, max(len(text), 1), 4000):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": text[i : i + 4000]}
            if buttons and i + 4000 >= len(text):
                payload["reply_markup"] = {"inline_keyboard": buttons}
            result = await self.call("sendMessage", payload)
        return result

    # --- Confirmer -----------------------------------------------------------------------

    async def confirm(self, chat_id: int, question: str) -> tuple[bool, str | None]:
        token = secrets.token_hex(4)
        fut: asyncio.Future[tuple[bool, str | None]] = asyncio.get_running_loop().create_future()
        self.pending[chat_id] = (token, fut)
        await self.send(
            chat_id,
            question,
            [
                [
                    {"text": "▶️ Запустить", "callback_data": f"c|{token}|y"},
                    {"text": "✖️ Нет", "callback_data": f"c|{token}|n"},
                ]
            ],
        )
        try:
            return await asyncio.wait_for(fut, self.s.confirm_timeout_s)
        except TimeoutError:
            await self.send(chat_id, "Не дождался ответа, запуск отменён.")
            return False, None
        finally:
            if self.pending.get(chat_id, (None,))[0] == token:
                del self.pending[chat_id]

    # --- updates -------------------------------------------------------------------------

    async def handle_update(self, update: dict[str, Any]) -> None:
        if cb := update.get("callback_query"):
            await self._on_callback(cb)
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text")
        if chat_id is None or not text:
            return
        if chat_id not in self.s.allowed_chats:
            log.info("ignoring chat %s (not in CHAT_ALLOWED_CHAT_IDS)", chat_id)
            return
        if text.startswith("/start") or text.startswith("/help"):
            await self.send(chat_id, HELP)
            return
        if text.startswith("/new"):
            if s := self.sessions.pop(chat_id, None):
                await s.close()
            await self.send(chat_id, "Начинаем заново.")
            return
        # While a confirmation is open, a typed answer settles it instead of queueing behind it:
        # the session is blocked on that very question.
        if chat_id in self.pending:
            _, fut = self.pending[chat_id]
            if not fut.done():
                fut.set_result((True, None) if is_yes(text) else (False, text))
            return
        replied = (msg.get("reply_to_message") or {}).get("text")
        self._spawn(self._run_turn(chat_id, with_reply_context(text, replied)))

    async def _on_callback(self, cb: dict[str, Any]) -> None:
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        parts = (cb.get("data") or "").split("|")
        answer = "Устарело"
        if chat_id in self.s.allowed_chats and len(parts) == 3 and parts[0] == "c":
            token, choice = parts[1], parts[2]
            pending = self.pending.get(chat_id)
            if pending and pending[0] == token and not pending[1].done():
                pending[1].set_result((choice == "y", None))
                answer = "Запускаю" if choice == "y" else "Отменено"
                await self.call(
                    "editMessageReplyMarkup",
                    {
                        "chat_id": chat_id,
                        "message_id": cb["message"]["message_id"],
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
        await self.call("answerCallbackQuery", {"callback_query_id": cb["id"], "text": answer})

    async def _run_turn(self, chat_id: int, text: str) -> None:
        lock = self.locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            session = self.sessions.get(chat_id)
            if session is None:
                session = self.sessions[chat_id] = self.make_session(chat_id, self)
            await self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

            async def reply(out: str) -> None:
                await self.send(chat_id, out)

            try:
                await session.send(text, reply)
            except Exception:
                log.exception("chat %s turn failed", chat_id)
                # A broken SDK subprocess would fail every later turn too; start fresh next time.
                self.sessions.pop(chat_id, None)
                await self.send(chat_id, "Что-то сломалось на моей стороне. Напишите ещё раз.")

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self) -> None:
        if not self.s.telegram_bot_token:
            raise SystemExit("CHAT_TELEGRAM_BOT_TOKEN is not set")
        if not self.s.allowed_chats:
            raise SystemExit("CHAT_ALLOWED_CHAT_IDS is empty: nobody could talk to the bot")
        # Long polling and a webhook are mutually exclusive per token.
        await self.call("deleteWebhook", {"drop_pending_updates": False})
        offset = 0
        while True:
            try:
                updates = await self.call(
                    "getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]}
                )
            except httpx.HTTPError as exc:
                log.warning("getUpdates failed: %s", type(exc).__name__)
                await asyncio.sleep(3)
                continue
            for u in updates or []:
                offset = u["update_id"] + 1
                try:
                    await self.handle_update(u)
                except Exception:
                    log.exception("update %s failed", u.get("update_id"))
