"""Task journal — one task's steps across every surface, so the agent keeps its place.

The idea comes from "Following the Agent" (Agentrail Research, 2026): one agent
journey crosses many surfaces (documents, command line, connected services,
the browser, sign-ins), and those surfaces share no common thread unless
something carries it. The paper's finding is that hard links, meaning a shared
identifier seen on both sides, are almost never wrong, while links that are
guessed afterwards from timing are wrong about a third of the time.

Inside our own agent we can carry the thread ourselves, so nothing has to be guessed:

- Every tool call of a task goes in the journal with its surface (files,
  command, web, browser, desktop, service...) and the identifiers it touched
  (file paths, web addresses, window titles). That is the task's journey.
- The journal travels with the answer. A follow-up message, a "save as skill"
  request and later recall all see what was really done, and where, instead
  of only the final text.
- Long tasks: old tool results are cleared from what the model re-reads
  (old screenshots first), and facts the agent wants to keep go in task notes
  (``keep_note``), which are never cleared. The model keeps working with a
  short context and does not lose the thread.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

MAX_NOTES = 30
KEEP_IMAGES = 2              # newest screenshots the model still sees
KEEP_RESULTS = 6             # newest tool results never cleared
TRIGGER_TOKENS = 60_000      # clear older results when the conversation grows past this
NEVER_CLEARED = ("keep_note", "use_skill")
CLEARED = ("[Earlier result removed to save space. What mattered is in your task notes (keep_note); "
           "run the tool again if you need it.]")

SURFACES = [
    ("browser_", "browser"), ("wait_for_user", "browser"), ("desktop_", "desktop"),
    ("run_command", "command"), ("fetch_url", "web"), ("web_search", "web"),
    ("list_files", "files"), ("read_file", "files"), ("search_files", "files"), ("write_file", "files"),
    ("edit_file", "files"), ("open_on_computer", "files"),
    ("remember", "memory"), ("search_memory", "memory"), ("forget", "memory"), ("keep_note", "notes"),
    ("use_skill", "skills"), ("save_skill", "skills"),
]
ID_KEYS = ("path", "url", "target", "window_title", "name", "query")


def surface_of(tool: str) -> str:
    for prefix, surface in SURFACES:
        if tool.startswith(prefix):
            return surface
    return "service"  # a connected service (MCP tool)


@dataclass
class Journal:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add(self, tool: str, args: Dict[str, Any], summary: str, ok: bool) -> None:
        touched = []
        for key in ID_KEYS:
            value = (args or {}).get(key)
            if isinstance(value, str) and value.strip() and not (tool.startswith("desktop_open") and key == "query"):
                touched.append(value.strip()[:120])
        self.steps.append({"surface": surface_of(tool), "tool": tool, "summary": summary, "ok": ok, "touched": touched})

    def note(self, text: str) -> str:
        from agentd.computer.tools import scrub_secrets

        text = scrub_secrets(" ".join((text or "").split()))[:400]
        if text and text not in self.notes:
            self.notes.append(text)
            del self.notes[:-MAX_NOTES]
        return "Task notes:\n" + "\n".join(f"- {n}" for n in self.notes) if self.notes else "No notes yet."

    def path(self) -> List[str]:
        """The surfaces in the order the task moved through them: files -> browser -> desktop."""
        order: List[str] = []
        for step in self.steps:
            if step["surface"] not in ("notes", "skills") and (not order or order[-1] != step["surface"]):
                order.append(step["surface"])
        return order

    def text(self, limit: int = 2500) -> str:
        """A short account of the task's journey, for follow-ups, skills and recall."""
        if not self.steps and not self.notes:
            return ""
        lines = [f"{i}. [{s['surface']}] {s['summary']}" + ("" if s["ok"] is not False else " (did not work)")
                 for i, s in enumerate(self.steps, 1) if s["surface"] != "notes"]
        if len(lines) > 25:  # the start and the end matter most
            lines = lines[:8] + [f"... {len(lines) - 20} more steps ..."] + lines[-12:]
        touched = list(dict.fromkeys(t for s in self.steps for t in s["touched"]))
        out = "\n".join(lines)
        if self.path():
            out += "\nPath: " + " -> ".join(self.path())
        if touched:
            out += "\nTouched: " + "; ".join(touched[:15])
        if self.notes:
            out += "\nNotes: " + " | ".join(self.notes[-10:])
        return out[:limit]


def build_note_tool(journal: Journal) -> StructuredTool:
    async def keep_note(note: str) -> str:
        """Write down a fact you will need later in this task (an order number, a price, a file name, what is left to do). Old tool results are removed in long tasks, but task notes stay. Returns all notes so far."""
        return journal.note(note)

    return StructuredTool.from_function(coroutine=keep_note, name="keep_note", description=keep_note.__doc__)


# ------------------------------------------------------------- context saving


def _has_image(message: ToolMessage) -> bool:
    return isinstance(message.content, list) and any(
        isinstance(block, dict) and block.get("type") in ("image", "image_url") for block in message.content)


def _without_images(message: ToolMessage) -> ToolMessage:
    kept = [block for block in message.content
            if not (isinstance(block, dict) and block.get("type") in ("image", "image_url"))]
    kept.append({"type": "text", "text": "[Older screenshot removed to save space; take a new one if you need to look again.]"})
    return message.model_copy(update={"content": kept})


@dataclass
class ShrinkOldResults:
    """A LangChain context edit: drop old screenshots always, and old tool results once the task gets long."""

    keep_images: int = KEEP_IMAGES
    keep: int = KEEP_RESULTS
    trigger: int = TRIGGER_TOKENS
    exclude_tools: Sequence[str] = NEVER_CLEARED

    def apply(self, messages: list, *, count_tokens) -> None:
        tools = [(i, m) for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
        with_images = [i for i, m in tools if _has_image(m)]
        for i in with_images[:-self.keep_images] if self.keep_images else with_images:
            messages[i] = _without_images(messages[i])

        if count_tokens(messages) <= self.trigger:
            return
        for i, message in tools[:-self.keep] if self.keep else tools:
            if (message.name or "") in self.exclude_tools or message.content == CLEARED:
                continue
            if isinstance(message.content, str) and len(message.content) < 300:
                continue  # short results cost little and often matter ("Saved.", "Clicked ...")
            messages[i] = message.model_copy(update={"content": CLEARED, "artifact": None})


def context_middleware():
    """Middleware for create_agent, or None if this LangChain has no context editing."""
    try:
        from langchain.agents.middleware import ContextEditingMiddleware
    except ImportError:
        return None
    return ContextEditingMiddleware(edits=[ShrinkOldResults()])

