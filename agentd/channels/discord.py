"""Discord channel — gateway bot, outbound connection (no public URL needed).

Env vars:
- DISCORD_BOT_TOKEN: from the Discord Developer Portal (enable the
  MESSAGE CONTENT privileged intent for the application).
- DISCORD_ALLOWED_CHANNEL_IDS: optional comma-separated channel ids. When
  empty, the bot answers in any channel it can see (plus DMs).

Inbound messages become tasks; replies carry the verification badge.
Below-threshold results arrive as Approve/Reject message components.
"""

import asyncio
import logging
import os
from typing import Any, Optional, Set

import discord

from agentd.channels.base import MessagingChannel, format_approval_card

logger = logging.getLogger("agentd.channels.discord")


class ApprovalView(discord.ui.View):
    """Approve/Reject buttons bound to one pending approval."""

    def __init__(self, manager, approval_id: str):
        super().__init__(timeout=None)
        self.manager = manager
        self.approval_id = approval_id

    async def _resolve(self, interaction: discord.Interaction, decision: str) -> None:
        note = await self.manager.handle_approval_callback(
            self.approval_id,
            decision,
            resolved_by=f"discord:{interaction.user}",
        )
        await interaction.response.edit_message(content=note, view=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "approved")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "rejected")


class DiscordChannel(MessagingChannel):
    """discord.py gateway client running inside the FastAPI event loop."""

    name = "discord"

    def __init__(self, token: Optional[str] = None, allowed_channel_ids: Optional[str] = None):
        super().__init__()
        self.token = token or os.getenv("DISCORD_BOT_TOKEN")
        raw = (
            allowed_channel_ids
            if allowed_channel_ids is not None
            else os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "")
        )
        self.allowed: Set[int] = {int(x) for x in raw.replace(" ", "").split(",") if x}
        self._client: Optional[discord.Client] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self.configured:
            return
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._register_events()
        self._task = asyncio.create_task(self._client.start(self.token))
        logger.info("Discord gateway connecting (allowed channels: %s)", self.allowed or "any")

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        if self._task:
            self._task.cancel()

    def _register_events(self) -> None:
        assert self._client is not None
        client = self._client

        @client.event
        async def on_ready():  # pragma: no cover - network lifecycle
            logger.info(f"Discord bot ready as {client.user}")

        @client.event
        async def on_message(message: discord.Message):  # pragma: no cover - network lifecycle
            if message.author.bot or not (message.content or "").strip():
                return
            is_dm = isinstance(message.channel, discord.DMChannel)
            if self.allowed and not is_dm and message.channel.id not in self.allowed:
                return
            assert self.manager is not None
            reply = await self.manager.handle_text(self, message.content, str(message.channel.id))
            if reply:
                await message.reply(reply[:2000])

    # ---------------------------------------------------------------- cards

    async def send_approval(self, approval) -> None:
        channel = self._get_channel(approval.chat_ref)
        if channel is None:
            logger.warning(f"Discord channel {approval.chat_ref} not found for approval")
            return
        view = ApprovalView(self.manager, approval.id)
        await channel.send(format_approval_card(approval)[:2000], view=view)

    async def send_text(self, chat_ref: str, text: str) -> None:
        channel = self._get_channel(chat_ref)
        if channel is not None:
            await channel.send(text[:2000])

    def _get_channel(self, chat_ref: str) -> Optional[Any]:
        if self._client is None:
            return None
        try:
            return self._client.get_channel(int(chat_ref))
        except (TypeError, ValueError):
            return None
