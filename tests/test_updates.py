"""In-app update check and install, without the internet."""

import subprocess

import httpx
import pytest

from agentd import updates


def github(release=None, status=200):
    def handler(request):
        assert request.url.path == "/repos/Adityakk9031/agentd-desktop/releases/latest"
        return httpx.Response(status, json=release or {})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AGENTD_UPDATE_REPO", raising=False)
    monkeypatch.delenv("AGENTD_PACKAGED", raising=False)
    updates._cache.update(at=0.0, data=None)
    monkeypatch.setattr(updates, "__version__", "0.2.0")


RELEASE = {"tag_name": "v0.3.0", "body": "Faster agents", "html_url": "https://github.com/Adityakk9031/agentd-desktop/releases/tag/v0.3.0",
           "prerelease": False, "assets": [{"name": "agentd-0.3.0-py3-none-any.whl",
           "browser_download_url": "https://github.com/Adityakk9031/agentd-desktop/releases/download/v0.3.0/agentd-0.3.0-py3-none-any.whl"}]}


def test_a_newer_release_is_offered():
    info = updates.check(force=True, transport=github(RELEASE))
    assert info["newer"] is True and info["latest"] == "0.3.0" and info["current"] == "0.2.0"
    assert info["notes"] == "Faster agents" and info["wheel"].endswith(".whl")


@pytest.mark.parametrize("tag, prerelease", [("v0.2.0", False), ("v0.1.9", False), ("v0.9.0", True), ("nightly", False)])
def test_same_older_prerelease_or_odd_tags_are_not_offered(tag, prerelease):
    info = updates.check(force=True, transport=github({**RELEASE, "tag_name": tag, "prerelease": prerelease}))
    assert info["newer"] is False


def test_no_releases_yet_is_not_an_error_for_the_user():
    info = updates.check(force=True, transport=github(status=404))
    assert info["newer"] is False and "No releases" in info["error"]


def test_source_checkouts_are_never_overwritten():
    info = {**updates.check(force=True, transport=github(RELEASE)), "install": "source"}
    ran = []
    result = updates.install(info, runner=lambda *a, **k: ran.append(a))
    assert result["ok"] is False and "git pull" in result["message"] and ran == []


def test_pip_installs_upgrade_from_the_release_wheel_then_restart():
    info = {**updates.check(force=True, transport=github(RELEASE)), "install": "pip"}
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = updates.install(info, runner=runner)
    assert result == {"ok": True, "restart": True, "message": "Version 0.3.0 is installed. Restarting…"}
    assert calls[0][-1] == RELEASE["assets"][0]["browser_download_url"] and "--upgrade" in calls[0]


def test_without_a_wheel_the_tagged_source_is_installed():
    info = {**updates.check(force=True, transport=github({**RELEASE, "assets": []})), "install": "pip"}
    calls = []
    updates.install(info, runner=lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    assert calls[0][-1] == "git+https://github.com/Adityakk9031/agentd-desktop.git@v0.3.0"


def test_a_wheel_hosted_anywhere_else_is_refused():
    bad = {**RELEASE, "assets": [{"name": "x.whl", "browser_download_url": "https://evil.example/x.whl"}]}
    info = {**updates.check(force=True, transport=github(bad)), "install": "pip"}
    ran = []
    result = updates.install(info, runner=lambda *a, **k: ran.append(a))
    assert result["ok"] is False and ran == []


def test_a_failed_install_reports_why_and_does_not_restart():
    info = {**updates.check(force=True, transport=github(RELEASE)), "install": "pip"}
    result = updates.install(info, runner=lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "No space left on device"))
    assert result["ok"] is False and "No space left" in result["message"] and not result.get("restart")


def test_this_checkout_counts_as_source():
    assert updates.install_kind() == "source"


def test_the_app_and_the_runtime_carry_the_same_version():
    import json
    from pathlib import Path

    import agentd

    package = json.loads((Path(__file__).resolve().parent.parent / "desktop" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == agentd.__version__  # the installer name and the update check must agree
