"""The task journal and context saving (from "Following the Agent", Agentrail 2026),
look-alike address warnings, picture-based desktop control and the control frame."""

import os
import sys
import time

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agentd.models.config import ModelsConfig, RoleConfig, parse_spec
from agentd.runtime.agent import Agent
from agentd.runtime.journal import CLEARED, Journal, ShrinkOldResults, surface_of
from agentd.safety.gate import ActionGate
from agentd.safety.lookalike import check as lookalike
from agentd.safety.permissions import PermissionPolicy
from tests.test_desktop import notepad  # noqa: F401  (fixture)

CONFIG = ModelsConfig(roles={"default": RoleConfig(parse_spec("fake:one"))})


class ToolModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(tool_name, i=0, **args):
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": f"c{i}"}])


# ------------------------------------------------------------------- journal


def test_each_step_is_filed_under_its_surface_with_what_it_touched():
    journal = Journal()
    journal.add("web_search", {"query": "flights to Goa"}, "Searching the web for flights to Goa", True)
    journal.add("browser_open", {"url": "https://shop.example/goa"}, "Browser: opening https://shop.example/goa", True)
    journal.add("desktop_switch", {"window_title": "Excel"}, "Desktop: switching to Excel", False)
    journal.add("write_file", {"path": "trip.md", "content": "..."}, "Writing trip.md", True)
    journal.add("github_search_repos", {"q": "x"}, "github search repos", True)

    assert [s["surface"] for s in journal.steps] == ["web", "browser", "desktop", "files", "service"]
    assert journal.path() == ["web", "browser", "desktop", "files", "service"]
    text = journal.text()
    assert "3. [desktop] Desktop: switching to Excel (did not work)" in text
    assert "Path: web -> browser -> desktop -> files -> service" in text
    assert "https://shop.example/goa" in text and "trip.md" in text and "Excel" in text


def test_notes_are_kept_once_without_secrets_and_capped():
    journal = Journal()
    journal.note("Order number 4471")
    assert journal.note("Order number 4471").count("4471") == 1
    assert "hunter2" not in journal.note("password is hunter2")
    for i in range(40):
        journal.note(f"fact {i}")
    assert len(journal.notes) == 30 and journal.notes[-1] == "fact 39"


def test_a_long_journey_keeps_its_start_and_end():
    journal = Journal()
    for i in range(40):
        journal.add("read_file", {"path": f"f{i}.txt"}, f"Reading f{i}.txt", True)
    text = journal.text()
    assert "Reading f0.txt" in text and "Reading f39.txt" in text and "more steps" in text
    assert surface_of("browser_click") == "browser" and surface_of("keep_note") == "notes"


# ------------------------------------------------------------ context saving


def _tool(i, content, name="read_file"):
    return ToolMessage(content=content, tool_call_id=f"c{i}", name=name)


def _image(i):
    return ToolMessage(content=[{"type": "text", "text": "shot"},
                                {"type": "image", "source_type": "base64", "mime_type": "image/png", "data": "A" * 5000}],
                       tool_call_id=f"s{i}", name="desktop_screenshot")


def test_only_the_newest_screenshots_stay():
    messages = [HumanMessage("go")] + [_image(i) for i in range(4)]
    ShrinkOldResults().apply(messages, count_tokens=lambda m: 0)
    kinds = [[b["type"] for b in m.content] for m in messages[1:]]
    assert kinds[:2] == [["text", "text"], ["text", "text"]] and kinds[2:] == [["text", "image"], ["text", "image"]]


def test_old_long_results_are_cleared_once_the_task_is_long_but_notes_and_recent_ones_stay():
    big = "x" * 2000
    messages = [HumanMessage("go"), _tool(0, big), _tool(1, "Saved.", "write_file"), _tool(2, big, "keep_note")]
    messages += [_tool(i, big) for i in range(3, 10)]
    edit = ShrinkOldResults(keep=6)

    edit.apply(messages, count_tokens=lambda m: 10)  # short task: nothing changes
    assert messages[1].content == big
    edit.apply(messages, count_tokens=lambda m: 10 ** 6)
    assert messages[1].content == CLEARED  # old and long
    assert messages[2].content == "Saved."  # short results cost little
    assert messages[3].content == big  # task notes are never cleared
    assert all(m.content == big for m in messages[-6:])  # the newest results stay


@pytest.mark.asyncio
async def test_the_answer_carries_the_journal_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_WORKSPACE", str(tmp_path))
    (tmp_path / "prices.txt").write_text("shoes 499", encoding="utf-8")
    model = ToolModel(messages=iter([call("read_file", 1, path="prices.txt"), call("keep_note", 2, note="Shoes cost 499"),
                                     AIMessage(content="Shoes cost 499.")]))
    result = await Agent(permissions=PermissionPolicy(), config=CONFIG, model_factory=lambda spec: model).run(
        "how much are the shoes?", gate=ActionGate(mode="auto"))
    assert "1. [files] Reading prices.txt" in result["journal"]
    assert "Notes: Shoes cost 499" in result["journal"] and "Touched: prices.txt" in result["journal"]


@pytest.mark.asyncio
async def test_a_follow_up_sees_the_steps_of_the_turn_before():
    seen = {}

    class Recorder(ToolModel):
        def _generate(self, messages, *args, **kwargs):
            seen.setdefault("messages", [m.content for m in messages])
            return super()._generate(messages, *args, **kwargs)

    history = [{"role": "user", "content": "save the report"},
               {"role": "assistant", "content": "Saved.\n\nSteps taken:\n1. [files] Writing report.md"}]
    model = Recorder(messages=iter([AIMessage(content="It is report.md")]))
    await Agent(permissions=PermissionPolicy(), config=CONFIG, model_factory=lambda spec: model).run(
        "which file was it?", history=history)
    assert any("1. [files] Writing report.md" in str(m) for m in seen["messages"])


# --------------------------------------------------------------- look-alike


@pytest.mark.parametrize("url", ["https://www.amazon.in/dp/1", "https://paypal.com/x", "https://github.io",
                                 "https://login.microsoftonline.com", "https://example.com", "http://127.0.0.1:8000",
                                 "https://chaser.com", "https://xn--mnchen-3ya.de"])
def test_real_and_unrelated_sites_get_no_warning(url):
    assert lookalike(url) is None


@pytest.mark.parametrize("url, brand", [("https://amaz0n.com", "amazon.com"), ("https://amazon-deals.shop", "amazon.com"),
                                        ("https://paypal.com.login-help.net", "paypal.com"),
                                        ("https://gooogle.com", "google.com"), ("https://rnicrosoft.com", "microsoft.com"),
                                        ("https://xn--pypal-4ve.com", "paypal.com"),
                                        ("https://xn--80ak6aa92e.com", "apple.com")])
def test_imitations_are_named(url, brand):
    warning = lookalike(url)
    assert warning and f"imitation of {brand}" in warning and "Do not sign in, pay" in warning


# ------------------------------------------------------- seeing by picture

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="desktop control is Windows-only for now")


def _tools(gate):
    from agentd.computer import desktop

    return {t.name: t for t in desktop.build_desktop_tools(gate)}


async def _content(tool, **args):
    message = await tool.ainvoke({"type": "tool_call", "id": "1", "name": tool.name, "args": args})
    return message.content


@windows_only
@pytest.mark.asyncio
async def test_clicking_and_typing_by_position_in_a_real_app(notepad):  # noqa: F811
    path, name = notepad
    t = _tools(ActionGate(mode="auto"))
    await t["desktop_switch"].ainvoke({"window_title": name})

    shot = await _content(t["desktop_screenshot"])
    assert [b["type"] for b in shot] == ["text", "image"] and "0 to 1000" in shot[0]["text"]
    zoom = await _content(t["desktop_zoom"], x1=0, y1=0, x2=500, y2=300)
    assert zoom[1]["type"] == "image" and "Close-up" in zoom[0]["text"]

    after = await _content(t["desktop_click_at"], x=500, y=600)  # the middle of the text area
    assert "Clicked at 500,600" in after[0]["text"] and after[1]["type"] == "image"
    await _content(t["desktop_type_text"], text="by position")
    await t["desktop_keys"].ainvoke({"keys": "{Ctrl}s"})
    for _ in range(20):
        time.sleep(0.25)
        if "by position" in open(path, encoding="utf-8").read():
            break
    assert "by position" in open(path, encoding="utf-8").read()


@windows_only
@pytest.mark.asyncio
async def test_position_actions_ask_first_and_bad_positions_are_refused(notepad):  # noqa: F811
    _, name = notepad
    events = []
    gate = ActionGate(on_event=events.append, interactive=False)
    t = _tools(gate)
    await t["desktop_switch"].ainvoke({"window_title": name})

    declined = await _content(t["desktop_click_at"], x=500, y=600)
    assert "declined" in str(declined)
    assert "ERROR" in str(await _content(t["desktop_click_at"], x=1500, y=10))
    assert "declined" in str(await _content(t["desktop_drag"], x1=10, y1=10, x2=50, y2=50))
    scrolled = await _content(t["desktop_scroll"], x=500, y=500, direction="down")  # scrolling only looks
    assert "Scrolled down" in str(scrolled)


@windows_only
def test_the_grid_spacing_fits_the_area():
    from agentd.computer.desktop import _grid_step

    assert _grid_step(1000) == 100 and _grid_step(500) == 50 and _grid_step(100) == 10 and _grid_step(40) == 5


# ---------------------------------------------------------- control frame


def test_the_frame_can_be_switched_off(monkeypatch):
    from agentd.computer import overlay

    monkeypatch.setenv("AGENTD_CONTROL_FRAME", "0")
    assert overlay.enabled() is False
    overlay.show_frame((0, 0, 100, 100))  # does nothing, raises nothing


@windows_only
def test_the_frame_is_drawn_outside_the_window_and_lets_clicks_through(monkeypatch):
    pytest.importorskip("tkinter")
    import ctypes
    from ctypes import wintypes

    from agentd.computer import overlay

    monkeypatch.setenv("AGENTD_CONTROL_FRAME", "1")
    user32 = ctypes.windll.user32

    def frame_windows():
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if pid.value == os.getpid() and name.value == "TkTopLevel" and user32.IsWindowVisible(hwnd):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append(((rect.left, rect.top, rect.right, rect.bottom), user32.GetWindowLongW(hwnd, -20)))
            return True

        user32.EnumWindows(visit, 0)
        return found

    overlay.show_frame((300, 300, 700, 600))
    shown = []
    for _ in range(50):
        time.sleep(0.1)
        shown = frame_windows()
        if len(shown) >= 5:
            break
    assert len(shown) >= 5  # four edges and the tab
    for (left, top, right, bottom), style in shown:
        assert style & 0x20 and style & 0x08000000  # clicks pass through; never takes the focus
        inside = left >= 300 and right <= 700 and top >= 300 and bottom <= 600
        assert not inside  # nothing is drawn over the window itself
    overlay.hide_frame_soon()
    for _ in range(40):
        time.sleep(0.1)
        if not frame_windows():
            break
    assert not frame_windows()
