"""The "agent in control" frame: a coloured border around the window the agent is using.

While the agent clicks or types in a desktop app, a thin orange frame and a
small "agentd is using this window" tab sit just outside that window, so you can
see at a glance that it is the agent moving things (idea from Windows-MCP's
control overlay, MIT). The frame lets clicks pass through, never takes the
keyboard focus, and is drawn outside the window so it never covers what the
agent sees or clicks. It goes away a moment after the last action.

Windows only; anywhere else, or if it cannot be drawn, nothing happens.
Set AGENTD_CONTROL_FRAME=0 to switch it off.
"""

import os
import queue
import sys
import threading
from typing import Optional, Tuple

THICKNESS = 4
COLOUR = "#ff8a00"
LINGER_MS = 1500

_commands: "queue.Queue" = queue.Queue()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def enabled() -> bool:
    return sys.platform == "win32" and os.getenv("AGENTD_CONTROL_FRAME", "1") not in ("0", "false", "no")


def show_frame(rect: Tuple[int, int, int, int]) -> None:
    if enabled() and _start():
        _commands.put(("show", rect))


def hide_frame_soon() -> None:
    if _thread is not None:
        _commands.put(("hide", None))


def _start() -> bool:
    global _thread
    with _lock:
        if _thread is None:
            _thread = threading.Thread(target=_run, name="agentd-frame", daemon=True)
            _thread.start()
    return _thread.is_alive()


def _click_through(widget) -> None:
    import ctypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


def _run() -> None:
    try:
        import tkinter as tk
    except ImportError:
        return
    try:
        root = tk.Tk()
    except Exception:  # no display
        return
    root.withdraw()

    def bar(text: str = ""):
        top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=COLOUR)
        if text:
            tk.Label(top, text=text, bg=COLOUR, fg="black", font=("Segoe UI", 9, "bold"), padx=8).pack()
        top.withdraw()
        top.update_idletasks()
        _click_through(top)
        return top

    edges = [bar() for _ in range(4)]
    tab = bar("agentd is using this window")
    pending = {"hide": None}

    def place(rect):
        left, top_, right, bottom = rect
        t = THICKNESS
        width, height = right - left + 2 * t, bottom - top_ + 2 * t
        spots = [(left - t, top_ - t, width, t), (left - t, bottom, width, t),
                 (left - t, top_ - t, t, height), (right, top_ - t, t, height)]
        for edge, (x, y, w, h) in zip(edges, spots):
            edge.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
            edge.deiconify()
            edge.lift()
        tab.update_idletasks()
        tab.geometry(f"+{left}+{max(0, top_ - t - tab.winfo_reqheight())}")
        tab.deiconify()
        tab.lift()

    def hide():
        for widget in (*edges, tab):
            widget.withdraw()

    def poll():
        try:
            while True:
                command, rect = _commands.get_nowait()
                if pending["hide"] is not None:
                    root.after_cancel(pending["hide"])
                    pending["hide"] = None
                if command == "show":
                    place(rect)
                else:
                    pending["hide"] = root.after(LINGER_MS, hide)
        except queue.Empty:
            pass
        root.after(50, poll)

    root.after(50, poll)
    root.mainloop()
