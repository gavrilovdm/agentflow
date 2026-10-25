"""Telegram: run notifications and inline Approve/Reject buttons for HITL checkpoints.

Button presses arrive at POST /webhooks/telegram (TELEGRAM_MODE=webhook) or are pulled by
`poll` (TELEGRAM_MODE=polling), and resume the paused graph thread.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from agentflow.config import get_settings

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


async def _call(method: str, payload: dict[str, Any]) -> httpx.Response | None:
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        log.info("telegram not configured; skipping %s", method)
        return None
    # A notification must never fail the job that sends it: a transient network error once
    # made a worker job "fail" after the run had already paused correctly, and the human was
    # never told. Retry once, then log and move on.
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                return await client.post(API.format(token=s.telegram_bot_token, method=method), json=payload)
        except httpx.HTTPError as exc:
            log.warning("telegram %s attempt %s failed: %s", method, attempt, type(exc).__name__)
    return None


async def send_message(text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": get_settings().telegram_chat_id,
        "text": text[:4000],
        "parse_mode": "Markdown",
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    resp = await _call("sendMessage", payload)
    # Failure messages embed raw tool output (stray * and _) that Telegram rejects as bad
    # Markdown with a 400 — losing exactly the message that mattered. Resend as plain text.
    if resp is not None and resp.status_code == 400:
        payload.pop("parse_mode")
        resp = await _call("sendMessage", payload)
    if resp is not None and resp.status_code >= 400:
        log.warning("telegram error %s: %s", resp.status_code, resp.text[:300])


async def request_approval(thread_id: str, kind: str, summary: str) -> None:
    await send_message(
        f"🟡 *Approval needed* — {kind}\n\n{summary}\n\nthread: `{thread_id}`",
        buttons=[
            [
                {"text": "✅ Approve", "callback_data": f"approve|{thread_id}"},
                {"text": "❌ Reject", "callback_data": f"reject|{thread_id}"},
            ]
        ],
    )


FAILURE_BUTTONS = {"retry": "🔁 Retry", "replan": "🗺 Re-plan", "skip": "⏭ Skip", "abort": "🛑 Stop"}


async def request_failure_decision(thread_id: str, dossier_text: str, options: list[str]) -> None:
    """A task ran out of budget: show the evidence and ask what to do."""
    await send_message(
        f"🔴 *A task needs you*\n\n{dossier_text}\n\n"
        f"Add guidance with `/hint {thread_id} <what to do differently>` (retries) or "
        f"`/replan {thread_id} <how to restructure>`.\nthread: `{thread_id}`",
        buttons=[[{"text": FAILURE_BUTTONS[o], "callback_data": f"{o}|{thread_id}"} for o in options]],
    )


async def answer_callback(callback_id: str, text: str) -> None:
    await _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


POLL_TIMEOUT_S = 50
POLL_RETRY_S = 5


async def poll(handle: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
    """Long-poll getUpdates forever and hand each update to `handle`.

    A bad token is fatal (every retry would fail the same way, so we stop and say so);
    anything else is retried after a pause rather than in a tight loop.
    """
    s = get_settings()
    api = API.format(token=s.telegram_bot_token, method="{}")
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S + 15) as client:
        # Polling and a webhook are mutually exclusive per bot.
        await client.post(api.format("deleteWebhook"), json={"drop_pending_updates": False})
        offset = 0
        while True:
            try:
                r = await client.post(
                    api.format("getUpdates"),
                    json={
                        "offset": offset,
                        "timeout": POLL_TIMEOUT_S,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("telegram getUpdates failed: %s", type(exc).__name__)
                await asyncio.sleep(POLL_RETRY_S)
                continue
            if r.status_code == 401:
                log.error("telegram polling stopped: TELEGRAM_BOT_TOKEN was rejected (401)")
                return
            if not body.get("ok"):
                log.warning("telegram getUpdates error %s: %s", r.status_code, str(body)[:200])
                await asyncio.sleep(POLL_RETRY_S)
                continue
            for update in body.get("result", []):
                offset = update["update_id"] + 1
                try:
                    await handle(update)
                except Exception:
                    log.exception("telegram update %s failed", update.get("update_id"))
