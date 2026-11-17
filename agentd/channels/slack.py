"""Slack channel — Socket Mode, outbound websocket (no public URL needed).

Env vars:
- SLACK_APP_TOKEN: app-level token starting with `xapp-` (enable Socket Mode
  in the app config and subscribe to `message` events).
- SLACK_BOT_TOKEN: bot token starting with `xoxb-` (scopes: chat:write,
  app_mentions:read...).
- SLACK_ALLOWED_CHANNELS: optional comma-separated channel ids. When empty,
  the bot answers wherever it is a member.

Inbound messages become tasks; replies carry the verification badge.
Below-threshold results arrive as Block Kit Approve/Reject buttons.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from agentd.channels.base import MessagingChannel, format_approval_card

logger = logging.getLogger("agentd.channels.slack")


class SlackChannel(MessagingChannel):
    """slack_sdk Socket Mode client; events hop from its thread to the main loop."""

    name = "slack"

    def __init__(
        self,
        app_token: Optional[str] = None,
        bot_token: Optional[str] = None,
        allowed_channels: Optional[str] = None,
    ):
        super().__init__()
        self.app_token = app_token or os.getenv("SLACK_APP_TOKEN")  # xapp-
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")  # xoxb-
        raw = (
            allowed_channels
            if allowed_channels is not None
            else os.getenv("SLACK_ALLOWED_CHANNELS", "")
        )
        self.allowed = {x.strip() for x in raw.split(",") if x.strip()}
        self._client: Optional[Any] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def configured(self) -> bool:
        return bool(self.app_token and self.bot_token)

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self.configured:
            return
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.web import WebClient

        self._main_loop = asyncio.get_running_loop()
        self._client = SocketModeClient(
            app_token=self.app_token,
            web_client=WebClient(token=self.bot_token),
        )
        self._client.socket_mode_request_listeners.append(self._on_request)
        self._client.connect()  # starts its own websocket thread
        logger.info("Slack Socket Mode connected (allowed channels: %s)", self.allowed or "all member channels")

    async def stop(self) -> None:
        if self._client:
            self._client.close()

    # -------------------------------------------------------------- events

    def _on_request(self, client: Any, req: Any) -> None:
        """Runs on the socket-mode thread — hop into the main asyncio loop."""
        if self._main_loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._handle_request(client, req), self._main_loop)

    async def _handle_request(self, client: Any, req: Any) -> None:
        try:
            if req.type == "events_api":
                await self._on_event(client, req)
            elif req.type == "interactive":
                await self._on_action(client, req)
        finally:
            try:
                client.send_socket_mode_response({"envelope_id": req.envelope_id})
            except Exception as exc:  # pragma: no cover - network lifecycle
                logger.warning(f"Slack ack failed: {exc}")

    async def _on_event(self, client: Any, req: Any) -> None:
        event = (req.payload or {}).get("event") or {}
        if event.get("type") != "message" or event.get("bot_id") or event.get("subtype"):
            return  # ignore our own messages and edits
        if self.allowed and event.get("channel") not in self.allowed:
            return
        assert self.manager is not None
        reply = await self.manager.handle_text(self, event.get("text", ""), event.get("channel", ""))
        if reply:
            self._post(event["channel"], reply)

    async def _on_action(self, client: Any, req: Any) -> None:
        payload = req.payload or {}
        user = (payload.get("user") or {}).get("username") or "?"
        for action in payload.get("actions", []):
            decision, _, approval_id = (action.get("action_id") or "").partition(":")
            if decision not in ("approve", "reject") or not approval_id:
                continue
            note = await self.manager.handle_approval_callback(
                approval_id,
                "approved" if decision == "approve" else "rejected",
                resolved_by=f"slack:{user}",
            )
            channel = (payload.get("channel") or {}).get("id")
            if channel:
                self._post(channel, note)

    # ---------------------------------------------------------------- posts

    def _post(self, channel: str, text: str, blocks: Optional[List[Dict[str, Any]]] = None) -> None:
        if self._client is None:
            return
        try:
            kwargs: Dict[str, Any] = {"channel": channel, "text": text[:4000]}
            if blocks:
                kwargs["blocks"] = blocks
            self._client.web_client.chat_postMessage(**kwargs)
        except Exception as exc:
            logger.warning(f"Slack post failed: {exc}")

    async def send_approval(self, approval) -> None:
        self._post(
            approval.chat_ref,
            format_approval_card(approval),
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": format_approval_card(approval)[:2900]},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "style": "primary",
                            "action_id": f"approve:{approval.id}",
                            "text": {"type": "plain_text", "text": "✅ Approve", "emoji": True},
                        },
                        {
                            "type": "button",
                            "style": "danger",
                            "action_id": f"reject:{approval.id}",
                            "text": {"type": "plain_text", "text": "❌ Reject", "emoji": True},
                        },
                    ],
                },
            ],
        )

    async def send_text(self, chat_ref: str, text: str) -> None:
        self._post(chat_ref, text)
