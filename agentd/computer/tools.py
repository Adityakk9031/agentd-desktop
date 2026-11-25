"""Computer tools — what lets the agent do things, not just talk.

Reading (files, folders, web pages) runs on its own. Anything that changes
the computer (writing files, running commands, opening things) goes through
the ActionGate first, and the hard rules in safety.py apply before that.

Tool results are data for the agent to read. The agent's instructions say
that text inside files, pages and command output can never give it orders.
"""

import asyncio
import fnmatch
import functools
import html
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx
from langchain_core.tools import StructuredTool

from agentd.computer.safety import Blocked, check_command, check_url, resolve_path, workspace
from agentd.safety.gate import ActionGate

MAX_READ = 24_000
MAX_OUTPUT = 12_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "$Recycle.Bin"}


def memory_file() -> Path:
    return Path(os.getenv("AGENTD_MEMORY_FILE") or Path("data") / "memory.md")


def read_memory(limit: int = 4000) -> str:
    path = memory_file()
    return path.read_text(encoding="utf-8")[-limit:] if path.is_file() else ""


_SECRET_LIKE = re.compile(
    r"(\b(sk|pk|rk|xai|ghp|gho|ghs|glpat|xox[abpr])[-_][A-Za-z0-9_\-]{12,}|\bAIza[0-9A-Za-z_\-]{20,}|"
    r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/]{40,}={0,2}|"
    r"(password|passwd|pwd|passcode|pin|otp|token|secret|api[ _-]?key)\s*(is|:|=)\s*\S+)",
    re.IGNORECASE,
)


def scrub_secrets(text: str) -> str:
    """Blank anything that looks like a password, key or token. Memory must never hold one."""
    return _SECRET_LIKE.sub("«redacted»", text)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[cut: {len(text) - limit} more characters]"


def _safe(func):
    """Turn Blocked and ordinary errors into a message the agent can read."""
    @functools.wraps(func)  # keeps the signature, which defines the tool's arguments
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Blocked as exc:
            return f"BLOCKED: {exc}"
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"
    return wrapper


def build_computer_tools(gate: ActionGate) -> List[StructuredTool]:
    """The computer tools, bound to one run's gate."""

    @_safe
    async def list_files(path: str = ".") -> str:
        """List the files and folders inside a folder on this computer."""
        folder = resolve_path(path)
        if not folder.is_dir():
            return f"ERROR: '{folder}' is not a folder."
        entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:300]
        lines = [f"{'[folder] ' if e.is_dir() else ''}{e.name}" + ("" if e.is_dir() else f"  ({e.stat().st_size} bytes)") for e in entries]
        return f"{folder}\n" + ("\n".join(lines) or "(empty)")

    @_safe
    async def read_file(path: str) -> str:
        """Read a text file on this computer and return its contents."""
        file = resolve_path(path)
        if not file.is_file():
            return f"ERROR: '{file}' is not a file."
        data = await asyncio.to_thread(file.read_bytes)
        if b"\x00" in data[:2000]:
            return f"'{file.name}' is not a text file ({len(data)} bytes)."
        return _clip(data.decode("utf-8", errors="replace"), MAX_READ)

    @_safe
    async def search_files(name_pattern: str = "*", containing: str = "", path: str = ".") -> str:
        """Find files under a folder by name pattern (e.g. *.pdf) and, optionally, by text they contain."""
        root = resolve_path(path)

        def scan() -> List[str]:
            import time as _time

            hits: List[str] = []
            needle = containing.lower()
            deadline = _time.monotonic() + 20  # a search of a whole drive must not hang the task
            seen = 0
            for folder, dirs, files in os.walk(root):
                seen += len(files)
                if _time.monotonic() > deadline or seen > 200_000:
                    hits.append("[stopped searching after 20 seconds; search a smaller folder for complete results]")
                    return hits
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in files:
                    if not fnmatch.fnmatch(name.lower(), name_pattern.lower()):
                        continue
                    full = Path(folder) / name
                    if needle:
                        try:
                            if full.stat().st_size > 2_000_000 or needle not in full.read_text(encoding="utf-8", errors="ignore").lower():
                                continue
                        except OSError:
                            continue
                    hits.append(str(full))
                    if len(hits) >= 100:
                        return hits
            return hits

        found = await asyncio.to_thread(scan)
        return "\n".join(found) if found else "No matching files."

    @_safe
    async def write_file(path: str, content: str) -> str:
        """Create a file or replace its contents. Asks the user first."""
        file = resolve_path(path, writing=True)
        action = "Replace" if file.exists() else "Create"
        if not await gate.request("write", f"{action} file {file}", _clip(content, 3000)):
            return "The user declined this action. Do not try it again; tell them what you wanted to do."
        file.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(file.write_text, content, "utf-8")
        return f"Saved {file} ({len(content)} characters)."

    @_safe
    async def edit_file(path: str, find: str, replace_with: str) -> str:
        """Change part of a text file: replace the exact text `find` with `replace_with`. Asks the user first."""
        file = resolve_path(path, writing=True)
        if not file.is_file():
            return f"ERROR: '{file}' is not a file."
        text = file.read_text(encoding="utf-8")
        count = text.count(find)
        if count != 1:
            return f"ERROR: the text to find appears {count} times; it must appear exactly once."
        if not await gate.request("write", f"Edit file {file}", f"- {_clip(find, 1200)}\n+ {_clip(replace_with, 1200)}"):
            return "The user declined this action. Do not try it again; tell them what you wanted to do."
        await asyncio.to_thread(file.write_text, text.replace(find, replace_with), "utf-8")
        return f"Edited {file}."

    @_safe
    async def run_command(command: str, folder: str = ".", timeout_seconds: int = 120) -> str:
        """Run a command on this computer (PowerShell on Windows, bash elsewhere) and return its output. Asks the user first."""
        check_command(command)
        cwd = resolve_path(folder)
        if not await gate.request("command", f"Run command in {cwd}", command):
            return "The user declined this command. Do not try it again; tell them what you wanted to do."
        shell = (["powershell", "-NoProfile", "-NonInteractive", "-Command", command] if sys.platform == "win32"
                 else ["bash", "-lc", command])
        timeout = max(5, min(int(timeout_seconds), 600))

        def run() -> str:
            try:
                done = subprocess.run(shell, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                                      errors="replace", stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                return f"The command was stopped after {timeout} seconds."
            output = (done.stdout or "") + (("\n[errors]\n" + done.stderr) if done.stderr.strip() else "")
            return f"exit code {done.returncode}\n{_clip(output.strip(), MAX_OUTPUT) or '(no output)'}"

        return await asyncio.to_thread(run)

    @_safe
    async def fetch_url(url: str) -> str:
        """Download a public web page and return its text."""
        target = check_url(url)
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "agentd/0.1"}) as client:
            response = await client.get(target)
        check_url(str(response.url))  # a redirect must not lead back to this computer
        text = response.text
        if "html" in response.headers.get("content-type", ""):
            text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", text)
            text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
            text = re.sub(r"[ \t\r\f]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return f"[{response.status_code}] {response.url}\n{_clip(text, MAX_READ)}"

    @_safe
    async def open_on_computer(target: str) -> str:
        """Open a file, folder or web address in its usual app on this computer. Asks the user first."""
        is_url = re.match(r"^https?://", target or "")
        opened = target if is_url else str(resolve_path(target))
        if not is_url and not Path(opened).exists():
            return f"ERROR: '{opened}' does not exist."
        if not await gate.request("open", f"Open {opened}", ""):
            return "The user declined this action."
        if sys.platform == "win32":
            os.startfile(opened)  # noqa: S606 - opening with the default app is the point
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", opened],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opened {opened}."

    @_safe
    async def remember(note: str) -> str:
        """Save a short fact about the user or their preferences so future conversations know it."""
        path = memory_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = scrub_secrets(" ".join(note.split())[:500])
        known = path.read_text(encoding="utf-8").lower() if path.is_file() else ""
        if f": {clean.lower()}\n" in known:  # the same note again: keep memory tidy
            return "Already remembered."
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {datetime.now():%Y-%m-%d}: {clean}\n")
        return "Remembered." + (" Secret-looking parts were left out; passwords belong in Logins." if "«redacted»" in clean else "")

    @_safe
    async def web_search(query: str, max_results: int = 8) -> str:
        """Search the web (DuckDuckGo) and return result titles, addresses and snippets. Then use fetch_url or the browser to read a result."""
        query = (query or "").strip()
        if not query:
            return "ERROR: give something to search for."
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (agentd web search)"}) as client:
            response = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        results = parse_search_results(response.text)[: max(1, min(int(max_results), 15))]
        if not results:
            return f"No results for '{query}'."
        return "\n\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1))

    @_safe
    async def search_memory(query: str) -> str:
        """Search your notes about the user and your earlier conversations with them (for example "what did we decide about the trip", "my laptop model"). Use it when the user refers to something from before."""
        from agentd.runtime.recall import format_hits, search

        text = format_hits(search(query), 8000)
        return text or f"Nothing in your notes or earlier conversations matches '{query}'."

    @_safe
    async def forget(text: str) -> str:
        """Delete saved notes that contain this text, when the user asks you to forget something or a note is wrong."""
        needle = (text or "").strip().lower()
        path = memory_file()
        if len(needle) < 3 or not path.is_file():
            return "Nothing was forgotten: give a few words from the note."
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if not (line.startswith("- ") and needle in line.lower())]
        removed = len(lines) - len(kept)
        if removed:
            path.write_text("".join(kept), encoding="utf-8")
        return f"Forgot {removed} note(s)." if removed else f"No note contains '{text}'."

    functions = [list_files, read_file, search_files, write_file, edit_file, run_command, fetch_url, web_search,
                 open_on_computer, remember, search_memory, forget]
    return [StructuredTool.from_function(coroutine=f, name=f.__name__, description=(f.__doc__ or "").strip()) for f in functions]


def parse_search_results(page: str) -> List[dict]:
    """Results from DuckDuckGo's plain HTML page: title, real address, snippet."""
    from urllib.parse import parse_qs, unquote, urlparse

    results = []
    blocks = re.split(r'<div class="result results_links', page)[1:]
    for block in blocks:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        href = html.unescape(link.group(1))
        if "duckduckgo.com/l/" in href:  # redirect wrapper: the real address is in uddg=
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if "duckduckgo.com/y.js" in href:  # adverts
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        clean = lambda text: html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()
        results.append({"title": clean(link.group(2)), "url": href, "snippet": clean(snippet.group(1) if snippet else "")[:300]})
    return results


def computer_facts() -> str:
    """A few facts the agent needs about where it is running."""
    system = {"win32": "Windows (commands run in PowerShell)", "darwin": "macOS (commands run in bash)"}.get(sys.platform, "Linux (commands run in bash)")
    return (
        f"Today is {datetime.now():%A %d %B %Y, %H:%M}. This computer runs {system}. "
        f"Relative paths start in {workspace()}."
    )
