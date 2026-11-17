"""Slack read-only tool — lets the agent read channel history on demand.

Operations:
- list_channels:  channels the bot is a member of (or all visible)
- get_messages:   recent messages from one channel, with permalinks
- search_messages: client-side keyword search over fetched history

Uses https://slack.com/api with SLACK_BOT_TOKEN (xoxb-). Required bot scope:
channels:history (+ channels:read). Like the Discord tool, this works even
when the interactive Slack channel adapter is disabled — the Telegram
command-center can ask "what happened in #general today?".
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from agentd.tools.base_tool import ToolInterface, ToolResponse

logger = logging.getLogger("tool.slack")

API = "https://slack.com/api"


class SlackTool(ToolInterface):
    """Read-only Slack history access for the agent."""

    def __init__(self, token: Optional[str] = None):
        super().__init__("SlackTool")
        self.token = token or os.getenv("SLACK_BOT_TOKEN")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _require_token(self) -> Optional[ToolResponse]:
        if not self.token:
            return ToolResponse(ok=False, error="SLACK_BOT_TOKEN not configured")
        return None

    @staticmethod
    def _ok(response_json: Dict[str, Any]) -> ToolResponse:
        """Slack signals failures with ok:false + an error string."""
        if response_json.get("ok"):
            return ToolResponse(ok=True, status_code=200, data=response_json)
        return ToolResponse(
            ok=False,
            status_code=200,
            error=f"Slack API error: {response_json.get('error', 'unknown')}",
        )

    def call(self, args: Dict[str, Any]) -> ToolResponse:
        missing = self._require_token()
        if missing:
            return missing
        operation = args.get("operation")
        try:
            if operation == "list_channels":
                return self._list_channels()
            if operation == "get_messages":
                return self._get_messages(args)
            if operation == "search_messages":
                return self._search_messages(args)
            return ToolResponse(ok=False, error=f"Unknown operation: {operation!r}")
        except httpx.HTTPStatusError as exc:
            return self._handle_error(exc, "Slack API error")
        except httpx.HTTPError as exc:
            return self._handle_error(exc, "Slack request failed")

    # ----------------------------------------------------------- operations

    def _list_channels(self) -> ToolResponse:
        response = self._make_http_request(
            "GET", f"{API}/conversations.list",
            headers=self._headers(),
            params={"types": "public_channel,private_channel", "limit": 200},
        )
        result = self._ok(response.json())
        if not result.ok:
            return result
        channels = [
            {"id": ch["id"], "name": ch.get("name"), "is_member": ch.get("is_member", False)}
            for ch in result.data.get("channels", [])
        ]
        return ToolResponse(ok=True, status_code=200, data={"channels": channels, "total": len(channels)})

    def _get_messages(self, args: Dict[str, Any]) -> ToolResponse:
        channel = str(args.get("channel_id", "")).strip()
        if not channel:
            return ToolResponse(ok=False, error="channel_id is required")
        limit = max(1, min(int(args.get("limit", 50)), 200))

        response = self._make_http_request(
            "GET", f"{API}/conversations.history",
            headers=self._headers(),
            params={"channel": channel, "limit": limit},
        )
        result = self._ok(response.json())
        if not result.ok:
            return result

        raw = result.data.get("messages", []) or []
        messages = [self._shape_message(m, channel) for m in raw if not m.get("subtype")]
        return ToolResponse(ok=True, status_code=200, data={
            "channel_id": channel,
            "messages": messages,
            "total": len(messages),
        })

    def _search_messages(self, args: Dict[str, Any]) -> ToolResponse:
        query = str(args.get("query", "")).strip()
        channel = str(args.get("channel_id", "")).strip()
        if not query:
            return ToolResponse(ok=False, error="query is required for search_messages")
        if not channel:
            return ToolResponse(ok=False, error="channel_id is required (search is scoped to one channel)")

        result = self._get_messages({**args, "limit": min(int(args.get("limit", 100)), 200)})
        if not result.ok:
            return result

        needle = query.lower()
        matches = [
            m for m in result.data.get("messages", [])
            if needle in (m.get("text") or "").lower()
        ][: max(1, min(int(args.get("limit", 25)), 100))]
        return ToolResponse(ok=True, status_code=200, data={
            "query": query, "matches": matches, "total": len(matches),
        })

    # ------------------------------------------------------------- helpers

    def _shape_message(self, msg: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
        return {
            "id": msg.get("ts"),
            "user": msg.get("user", "unknown"),
            "text": (msg.get("text") or "")[:2000],
            "timestamp": msg.get("ts"),
            "link": msg.get("permalink") or self._permalink(channel_id, msg.get("ts")),
        }

    @staticmethod
    def _permalink(channel_id: str, ts: Optional[str]) -> str:
        if not ts:
            return ""
        return f"https://slack.com/archives/{channel_id}/p{str(ts).replace('.', '')}"
