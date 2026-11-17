"""Tests against a REAL MCP stdio session (no network, no API keys).

These exist because fakes once hid that the real adapter tools are
async-only. They start mcp_server.py as a subprocess and call real tools
with inputs that fail validation locally, so no external API is contacted.
"""

import pytest

import json

from agentd.tools.langchain_tools import mcp_tools_session


@pytest.mark.asyncio
async def test_real_session_loads_tools():
    async with mcp_tools_session() as tools:
        names = {t.name for t in tools}
    assert {"github_search_repos", "discord_get_messages", "slack_get_messages"} <= names


@pytest.mark.asyncio
async def test_real_tool_failure_is_reported_as_failure():
    """A real tool returning {"ok": false} must reach the agent as a failure."""
    from agentd.runtime.agent import _tool_failed
    from agentd.models.client import message_text

    async with mcp_tools_session() as tools:
        tool = next(t for t in tools if t.name == "discord_list_channels")
        raw = await tool.ainvoke({"guild_id": "not-a-snowflake"})
    text = raw if isinstance(raw, str) else "".join(b.get("text", "") for b in raw if isinstance(b, dict))
    assert _tool_failed(text)
    assert "guild_id" in json.loads(text)["error"]


@pytest.mark.asyncio
async def test_caller_errors_propagate_unchanged():
    """An error inside the session block must surface as itself, not a RuntimeError."""
    with pytest.raises(ValueError, match="real cause"):
        async with mcp_tools_session():
            raise ValueError("real cause")
