"""Discord read-only tool — lets the agent see inside a Discord server.

Operations:
- list_channels:  text channels visible to the bot
- get_messages:   recent messages from one channel, with permalinks
- search_messages: keyword search across a channel's recent history

Uses plain https://discord.com/api/v10 REST calls (no gateway), so it only
needs a bot token with the SERVER MEMBERS / view permissions — specifically
the bot must be in the target server with "Read Message History" permission.

DISCORD_BOT_TOKEN: same token the Discord channel adapter uses. The tool is
useful even when the interactive Discord channel is disabled — e.g. the
Telegram command-center asking "anything important in my Discord?".
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from agentd.tools.base_tool import ToolInterface, ToolResponse

logger = logging.getLogger("tool.discord")

API = "https://discord.com/api/v10"
_SNOWFLAKE = re.compile(r"^\d{5,25}$")


def _jump_link(guild_id: str, channel_id: str, message_id: str) -> str:
    """Permanent Discord URL that opens the exact message."""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


class DiscordTool(ToolInterface):
    """Read-only Discord access over REST for the agent."""

    def __init__(self, token: Optional[str] = None):
        super().__init__("DiscordTool")
        self.token = token or os.getenv("DISCORD_BOT_TOKEN")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bot {self.token}"}

    def _require_token(self) -> Optional[ToolResponse]:
        if not self.token:
            return ToolResponse(ok=False, error="DISCORD_BOT_TOKEN not configured")
        return None

    def call(self, args: Dict[str, Any]) -> ToolResponse:
        missing = self._require_token()
        if missing:
            return missing
        operation = args.get("operation")
        try:
            if operation == "list_channels":
                return self._list_channels(args)
            if operation == "get_messages":
                return self._get_messages(args)
            if operation == "search_messages":
                return self._search_messages(args)
            return ToolResponse(ok=False, error=f"Unknown operation: {operation!r}")
        except httpx.HTTPStatusError as exc:
            return self._handle_error(exc, "Discord API error")
        except httpx.HTTPError as exc:
            return self._handle_error(exc, "Discord request failed")

    # ----------------------------------------------------------- operations

    def _list_channels(self, args: Dict[str, Any]) -> ToolResponse:
        guild_id = str(args.get("guild_id", "")).strip()
        if not _SNOWFLAKE.match(guild_id):
            return ToolResponse(ok=False, error="guild_id must be a Discord server id (digits)")

        response = self._make_http_request("GET", f"{API}/guilds/{guild_id}/channels", headers=self._headers())
        channels = [
            {
                "id": str(ch["id"]),
                "name": ch["name"],
                "type": ch.get("type"),
                "position": ch.get("position"),
            }
            for ch in response.json()
            if ch.get("type") == 0  # text channels only
        ]
        return ToolResponse(ok=True, status_code=response.status_code, data={
            "guild_id": guild_id,
            "channels": channels,
        })

    def _get_messages(self, args: Dict[str, Any]) -> ToolResponse:
        channel_id = str(args.get("channel_id", "")).strip()
        if not _SNOWFLAKE.match(channel_id):
            return ToolResponse(ok=False, error="channel_id must be a Discord channel id (digits)")

        guild_id = str(args.get("guild_id", "") or "").strip()
        limit = max(1, min(int(args.get("limit", 50)), 100))
        params: Dict[str, Any] = {"limit": limit}
        messages = self._fetch_pages(channel_id, args.get("pages", 1), params)
        return ToolResponse(ok=True, status_code=200, data=self._shape_messages(messages, guild_id))

    def _search_messages(self, args: Dict[str, Any]) -> ToolResponse:
        channel_id = str(args.get("channel_id", "")).strip()
        query = str(args.get("query", "")).strip()
        if not _SNOWFLAKE.match(channel_id):
            return ToolResponse(ok=False, error="channel_id must be a Discord channel id (digits)")
        if not query:
            return ToolResponse(ok=False, error="query is required for search_messages")

        guild_id = str(args.get("guild_id", "") or "").strip()
        limit = max(1, min(int(args.get("limit", 25)), 100))
        hits: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {"limit": 100}
        needle = query.lower()
        for page in range(max(1, min(int(args.get("pages", 3)), 10))):
            batch = self._request_messages(channel_id, params)
            if not batch:
                break
            for msg in batch:
                content = (msg.get("content") or "")
                if needle in content.lower():
                    hits.append(self._shape_message(msg, guild_id))
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
            # Walk further back in history
            params["before"] = batch[-1]["id"]
        return ToolResponse(ok=True, status_code=200, data={
            "query": query,
            "matches": hits,
            "total": len(hits),
        })

    # ------------------------------------------------------------- helpers

    def _fetch_pages(self, channel_id: str, pages: int, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        all_messages: List[Dict[str, Any]] = []
        for _ in range(max(1, min(int(pages), 10))):
            batch = self._request_messages(channel_id, params)
            if not batch:
                break
            all_messages.extend(batch)
            params["before"] = batch[-1]["id"]
        return all_messages

    def _request_messages(self, channel_id: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        response = self._make_http_request(
            "GET", f"{API}/channels/{channel_id}/messages", headers=self._headers(), params=params
        )
        return response.json() or []

    def _shape_messages(self, messages: List[Dict[str, Any]], guild_id: str = "") -> Dict[str, Any]:
        return {
            "messages": [self._shape_message(m, guild_id) for m in messages],
            "total": len(messages),
            "hint": (
                "Pass guild_id on follow-up calls to get clickable jump links "
                "(list_channels returns it)."
                if not guild_id else ""
            ),
        }

    def _shape_message(self, msg: Dict[str, Any], guild_id: str = "") -> Dict[str, Any]:
        """One message, author resolved, with its permanent jump link."""
        channel_id = str(msg.get("channel_id", ""))
        author = msg.get("author") or {}
        return {
            "id": str(msg.get("id")),
            "channel_id": channel_id,
            "author": author.get("global_name") or author.get("username") or str(author.get("id", "unknown")),
            "is_bot": bool(author.get("bot")),
            "content": (msg.get("content") or "")[:2000],
            "timestamp": msg.get("timestamp"),
            "link": _jump_link(guild_id, channel_id, str(msg.get("id"))) if guild_id else "",
        }
