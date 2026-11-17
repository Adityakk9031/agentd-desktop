"""Offline tests for the Discord/Slack reading tools and the synthesis path."""

import pytest

from agentd.tools.discord_tool import DiscordTool
from agentd.tools.slack_tool import SlackTool

# Realistic-length Discord snowflakes (the tool enforces 5-25 digits)
GUILD = "555000111222333444"
CHANNEL = "100000000000000111"
MESSAGE = "999000000000000999"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def stub_http(tool, payloads):
    """Replace _make_http_request; pops one payload per call."""
    calls = []

    def fake(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse(payloads.pop(0) if payloads else {})

    tool._make_http_request = fake
    return calls


# ---------------------------------------------------------------- Discord


def test_discord_requires_token():
    tool = DiscordTool(token=None)
    tool.token = None
    result = tool.call({"operation": "list_channels", "guild_id": "123"})
    assert result.ok is False
    assert "not configured" in result.error


def test_discord_list_channels_filters_text():
    tool = DiscordTool(token="t")
    calls = stub_http(tool, [
        [{"id": CHANNEL, "name": "dev-talk", "type": 0, "position": 1},
         {"id": "110000000000000222", "name": "rules", "type": 4},  # category -> filtered
         {"id": "120000000000000333", "name": "general", "type": 0}],
    ])
    result = tool.call({"operation": "list_channels", "guild_id": GUILD})
    assert result.ok
    assert [c["name"] for c in result.data["channels"]] == ["dev-talk", "general"]
    assert calls[0]["url"].endswith(f"/guilds/{GUILD}/channels")


def test_discord_get_messages_shapes_links():
    tool = DiscordTool(token="t")
    stub_http(tool, [[
        {"id": MESSAGE, "channel_id": CHANNEL, "guild_id": GUILD,
         "content": "v2.1 released, bug in auth fixed",
         "timestamp": "2026-10-05T10:00:00Z",
         "author": {"id": "1", "username": "alice"}},
    ]])
    result = tool.call({"operation": "get_messages", "channel_id": CHANNEL, "guild_id": GUILD})
    assert result.ok
    msg = result.data["messages"][0]
    assert msg["author"] == "alice"
    assert msg["link"] == f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE}"


def test_discord_search_matches_and_links():
    tool = DiscordTool(token="t")
    stub_http(tool, [[
        {"id": "100000000000000001", "channel_id": CHANNEL, "content": "release v2 is out", "author": {"username": "a"}},
        {"id": "100000000000000002", "channel_id": CHANNEL, "content": "lunch anyone?", "author": {"username": "b"}},
        {"id": "100000000000000003", "channel_id": CHANNEL, "content": "BUG: login broken", "author": {"username": "c"}},
    ]])
    result = tool.call({"operation": "search_messages", "channel_id": CHANNEL,
                        "query": "release", "pages": 1, "guild_id": GUILD})
    assert result.ok
    assert result.data["total"] == 1
    assert result.data["matches"][0]["link"].startswith(f"https://discord.com/channels/{GUILD}/{CHANNEL}/100000000000000001")


def test_discord_rejects_bad_channel_id():
    tool = DiscordTool(token="t")
    result = tool.call({"operation": "get_messages", "channel_id": "not-a-snowflake"})
    assert result.ok is False
    assert "digits" in result.error


# ------------------------------------------------------------------ Slack


def test_slack_requires_token():
    tool = SlackTool(token=None)
    tool.token = None
    result = tool.call({"operation": "list_channels"})
    assert result.ok is False
    assert "not configured" in result.error


def test_slack_list_channels():
    tool = SlackTool(token="t")
    stub_http(tool, [{"ok": True, "channels": [{"id": "C1", "name": "general", "is_member": True}]}])
    result = tool.call({"operation": "list_channels"})
    assert result.ok
    assert result.data["channels"][0]["name"] == "general"


def test_slack_get_messages_shapes_permalink():
    tool = SlackTool(token="t")
    stub_http(tool, [{"ok": True, "messages": [
        {"ts": "1728000000.000100", "user": "U1", "text": "deploy done", "type": "message"},
        {"ts": "1.2", "user": "U2", "text": "join message", "subtype": "channel_join"},  # filtered
    ]}])
    result = tool.call({"operation": "get_messages", "channel_id": "C1"})
    assert result.ok
    assert result.data["total"] == 1
    msg = result.data["messages"][0]
    assert msg["link"] == "https://slack.com/archives/C1/p1728000000000100"


def test_slack_api_error_surfaces():
    tool = SlackTool(token="t")
    stub_http(tool, [{"ok": False, "error": "missing_scope"}])
    result = tool.call({"operation": "get_messages", "channel_id": "C1"})
    assert result.ok is False
    assert "missing_scope" in result.error
