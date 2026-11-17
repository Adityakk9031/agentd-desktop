"""Unit tests for code-enforced tool permissions."""

import pytest

from agentd.safety.permissions import PermissionPolicy, DEFAULT_ASK


def test_default_allows_read_tools():
    assert PermissionPolicy().check("weather_current")[0] == "allow"


@pytest.mark.parametrize("tool", ["github_delete_repo", "slack_send_message", "linkedin_post", "shop_purchase"])
def test_default_asks_for_destructive_or_outward_tools(tool):
    assert PermissionPolicy().check(tool)[0] == "ask"


def test_deny_beats_allow():
    policy = PermissionPolicy(allow=["github_*"], deny=["github_delete*"])
    assert policy.check("github_delete_repo")[0] == "deny"
    assert policy.check("github_get_repo")[0] == "allow"


def test_matching_is_case_insensitive():
    assert PermissionPolicy(deny=["weather_*"]).check("Weather_Current")[0] == "deny"


def test_default_can_be_strict():
    policy = PermissionPolicy(default="ask", ask=[], allow=["news_*"])
    assert policy.check("news_search")[0] == "allow"
    assert policy.check("anything_else")[0] == "ask"


def test_invalid_default_rejected():
    with pytest.raises(ValueError):
        PermissionPolicy(default="maybe")


def test_from_env(monkeypatch):
    monkeypatch.setenv("PERMISSION_DEFAULT", "deny")
    monkeypatch.setenv("PERMISSION_ALLOW", "github_get_*, weather_*")
    monkeypatch.delenv("PERMISSION_ASK", raising=False)
    monkeypatch.setenv("PERMISSION_DENY", "")

    policy = PermissionPolicy.from_env()

    assert policy.ask == DEFAULT_ASK
    assert policy.check("weather_forecast")[0] == "allow"
    assert policy.check("discord_get_messages")[0] == "deny"
