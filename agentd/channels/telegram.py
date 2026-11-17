"""Telegram channel — long-polling bot, no webhook or public URL needed.

Env vars:
- TELEGRAM_BOT_TOKEN: from @BotFather
- TELEGRAM_ALLOWED_CHAT_IDS: comma-separated chat ids allowed to talk to the
  bot (recommended; when empty, anyone who finds the bot can use it).

Inbound chat messages become tasks; replies carry the verification badge.
Below-threshold results arrive as inline-keyboard Approve/Reject cards.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from agentd.channels.base import MessagingChannel, format_approval_card

logger = logging.getLogger("agentd.channels.telegram")

API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramChannel(MessagingChannel):
    """Plain Bot API long-polling over httpx — zero extra dependencies."""

    name = "telegram"

    def __init__(self, token: Optional[str] = None, allowed_chat_ids: Optional[str] = None):
        super().__init__()
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        raw = (
            allowed_chat_ids
            if allowed_chat_ids is not None
            else os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        )
        self.allowed: set[int] = {
            int(x) for x in raw.replace(" ", "").split(",") if x
        }
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._offset = 0
        self._running = False
        self._jobs: set = set()

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self.configured:
            return
        self._client = httpx.AsyncClient(timeout=40)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram long-polling started (allowed chats: %s)", self.allowed or "anyone")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client:
            await self._client.aclose()

    # -------------------------------------------------------------- polling

    async def _poll_loop(self) -> None:
        assert self._client is not None
        while self._running:
            try:
                response = await self._client.get(
                    API_URL.format(token=self.token, method="getUpdates"),
                    params={
                        "offset": self._offset,
                        "timeout": 30,
                        "allowed_updates": '["message","callback_query"]',
                    },
                )
                data = response.json()
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Telegram poll error: {exc}")
                await asyncio.sleep(3)

    async def _handle_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message")
        if message and message.get("text"):
            # In the background: a running agent may ask a question, and the
            # button tap that answers it arrives as a later update.
            task = asyncio.create_task(self._handle_message_safely(message))
            self._jobs.add(task)
            task.add_done_callback(self._jobs.discard)

        callback = update.get("callback_query")
        if callback:
            await self._handle_callback(callback)

    async def _handle_message_safely(self, message: Dict[str, Any]) -> None:
        try:
            await self._handle_message(message)
        except Exception as exc:
            logger.warning(f"Telegram task failed: {exc}")
            await self._send(str(message["chat"]["id"]), f"Sorry, that did not work: {str(exc)[:300]}")

    async def _handle_message(self, message: Dict[str, Any]) -> None:
        chat_id = str(message["chat"]["id"])
        if self.allowed and int(chat_id) not in self.allowed:
            await self._send(chat_id, "⛔ You are not authorized to use this bot.")
            return
        assert self.manager is not None
        reply = await self.manager.handle_text(self, message["text"], chat_id)
        if reply:
            await self._send(chat_id, reply)

    async def _handle_callback(self, callback: Dict[str, Any]) -> None:
        decision, _, approval_id = (callback.get("data") or "").partition(":")
        if decision not in ("approve", "reject") or not approval_id:
            return
        user = (callback.get("from") or {}).get("username") or str((callback.get("from") or {}).get("id", "?"))
        status = await self.manager.handle_approval_callback(
            approval_id,
            "approved" if decision == "approve" else "rejected",
            resolved_by=f"telegram:{user}",
        )
        assert self._client is not None
        await self._client.post(
            API_URL.format(token=self.token, method="answerCallbackQuery"),
            json={"callback_query_id": callback.get("id"), "text": status[:190]},
        )
        message = callback.get("message") or {}
        if message.get("message_id") and message.get("chat"):
            marker = "✅ Approved" if decision == "approve" else "❌ Rejected"
            await self._client.post(
                API_URL.format(token=self.token, method="editMessageReplyMarkup"),
                json={
                    "chat_id": message["chat"]["id"],
                    "message_id": message["message_id"],
                    "reply_markup": {
                        "inline_keyboard": [[{"text": f"{marker} by @{user}", "callback_data": "done"}]]
                    },
                },
            )

    # ---------------------------------------------------------------- cards

    def _approval_keyboard(self, approval) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{approval.id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{approval.id}"},
                ]
            ]
        }

    async def send_approval(self, approval) -> None:
        await self._send(approval.chat_ref, format_approval_card(approval), reply_markup=self._approval_keyboard(approval))

    async def send_text(self, chat_ref: str, text: str) -> None:
        await self._send(chat_ref, text)

    async def _send(self, chat_id: str, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
        if self._client is None:
            return
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await self._client.post(API_URL.format(token=self.token, method="sendMessage"), json=payload)
        except Exception as exc:
            logger.warning(f"Telegram send failed: {exc}")
