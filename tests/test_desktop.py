"""Desktop control (Windows UI Automation). Real-app tests use Notepad with a temporary file of their own."""

import asyncio
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

from agentd.computer import desktop
from agentd.computer.safety import Blocked
from agentd.safety.gate import ActionGate

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="desktop control is Windows-only for now")


def test_tools_are_not_offered_where_unsupported(monkeypatch):
    monkeypatch.setattr(desktop, "available", False)
    assert desktop.build_desktop_tools(ActionGate()) == []


def test_own_window_title_rule_is_exact():
    assert desktop.OWN_TITLES.search("agentd")
    assert not desktop.OWN_TITLES.search("agentd-notes.txt - Notepad")


@pytest.mark.parametrize("title", ["Windows Security", "Credential Manager", "Bitwarden", "Registry Editor"])
def test_password_and_security_windows_are_protected(title):
    assert desktop.PROTECTED_TITLES.search(title)


@pytest.mark.parametrize("keys, ok", [("{Ctrl}s", True), ("{Alt}{F4}", True), ("hello", True),
                                      ("{Ctrl}{Alt}{Del}", True), ("rm -rf /", False), ("<script>", False)])
def test_key_names_are_checked(keys, ok):
    assert bool(desktop._KEY.match(keys)) is ok


# ------------------------------------------------------------------- real app


@pytest.fixture
def notepad():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    pytest.importorskip("uiautomation")
    path = os.path.join(tempfile.gettempdir(), f"agentd-test-{uuid.uuid4().hex[:8]}.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("start\n")
    subprocess.Popen(["notepad.exe", path])
    name = os.path.basename(path)
    import uiautomation as auto

    for _ in range(40):
        if any(name in (c.Name or "") for c in auto.GetRootControl().GetChildren()):
            break
        time.sleep(0.25)
    else:
        pytest.skip("Notepad did not open")
    yield path, name
    desktop.worker().window = None
    for control in auto.GetRootControl().GetChildren():  # close only our own test window
        if name in (control.Name or ""):
            try:
                control.GetWindowPattern().Close()
            except Exception:
                pass
    time.sleep(1)
    for _ in range(10):
        try:
            os.remove(path)
            break
        except OSError:
            time.sleep(0.3)


def tools(gate):
    return {t.name: t for t in desktop.build_desktop_tools(gate)}


@windows_only
@pytest.mark.asyncio
async def test_read_type_and_save_in_a_real_app(notepad):
    path, name = notepad
    t = tools(ActionGate(mode="auto"))

    shown = await t["desktop_switch"].ainvoke({"window_title": name})
    assert f"Window: {name}" in shown and "[ref=d" in shown and "information only" in shown
    box = re.search(r'(?:document|textbox) "[^"]*" \[ref=(d\d+)\]', shown).group(1)

    assert (await t["desktop_type"].ainvoke({"ref": box, "text": "typed by the agent"})).startswith("Typed")
    assert (await t["desktop_keys"].ainvoke({"keys": "{Ctrl}s"})).startswith("Pressed")
    for _ in range(20):
        time.sleep(0.25)
        if open(path, encoding="utf-8").read() == "typed by the agent":
            break
    assert open(path, encoding="utf-8").read() == "typed by the agent"


@windows_only
@pytest.mark.asyncio
async def test_actions_ask_first_and_a_no_changes_nothing(notepad):
    path, name = notepad
    events = []
    gate = ActionGate(on_event=events.append)
    t = tools(gate)
    shown = await t["desktop_switch"].ainvoke({"window_title": name})  # looking never asks
    assert events == []
    box = re.search(r'(?:document|textbox) "[^"]*" \[ref=(d\d+)\]', shown).group(1)

    async def deny():
        while not gate.pending:
            await asyncio.sleep(0.01)
        gate.resolve(next(iter(gate.pending)), "deny")

    asyncio.create_task(deny())
    result = await t["desktop_type"].ainvoke({"ref": box, "text": "should not appear"})
    assert "declined" in result and events[0]["kind"] == "desktop" and "should not appear" in events[0]["detail"]
    shown = await t["desktop_read"].ainvoke({})
    assert "should not appear" not in shown


@windows_only
@pytest.mark.asyncio
async def test_screenshot_returns_an_image(notepad):
    _, name = notepad
    t = tools(ActionGate(mode="auto"))
    await t["desktop_switch"].ainvoke({"window_title": name})
    message = await t["desktop_screenshot"].ainvoke({"type": "tool_call", "id": "1", "name": "desktop_screenshot", "args": {}})
    kinds = [block.get("type") for block in message.content]
    assert kinds == ["text", "image"] and len(message.content[1]["data"]) > 1000


@windows_only
@pytest.mark.asyncio
async def test_the_agent_cannot_control_its_own_window(monkeypatch):
    pytest.importorskip("uiautomation")

    class Window:
        Name = "agentd"
        ProcessId = 1

    with pytest.raises(Blocked):
        desktop._check_window(Window())

    class Vault:
        Name = "Bitwarden"
        ProcessId = 1

    with pytest.raises(Blocked):
        desktop._check_window(Vault())


@windows_only
@pytest.mark.asyncio
async def test_unknown_refs_and_bad_keys_are_refused(notepad):
    _, name = notepad
    t = tools(ActionGate(mode="auto"))
    await t["desktop_switch"].ainvoke({"window_title": name})
    assert "There is no d999" in await t["desktop_click"].ainvoke({"ref": "d999"})
    assert (await t["desktop_keys"].ainvoke({"keys": "rm -rf /"})).startswith("ERROR")
