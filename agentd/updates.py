"""In-app updates from this project's GitHub releases.

The app asks GitHub for the latest release of the project repository and
compares it with the version running here. If it is newer, the app offers
to update:

- **Installed with pip** (the normal case): the release's wheel, or the
  release tag itself, is installed with pip, then the app restarts.
- **Running from a source checkout** (a developer's clone): nothing is
  installed; the app says to `git pull` instead, so it never overwrites
  someone's working copy.
- **Packaged desktop app**: the release page opens, where the new installer is.

Nothing is downloaded or installed unless the person presses Update.
Releases come only from ``AGENTD_UPDATE_REPO`` (default: this project).
"""

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from packaging.version import InvalidVersion, Version

from agentd import __version__

logger = logging.getLogger("agentd.updates")

DEFAULT_REPO = "Adityakk9031/agentd-desktop"
CHECK_EVERY_SECONDS = 6 * 3600
_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def repo() -> str:
    value = (os.getenv("AGENTD_UPDATE_REPO") or DEFAULT_REPO).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError(f"AGENTD_UPDATE_REPO must look like owner/name, not {value!r}")
    return value


def parse_version(tag: str) -> Optional[Version]:
    try:
        return Version(tag.strip().lstrip("vV"))
    except InvalidVersion:
        return None


def install_kind() -> str:
    """'source' for a git checkout, 'packaged' inside the desktop installer, else 'pip'."""
    if os.getenv("AGENTD_PACKAGED") == "1":
        return "packaged"
    package_dir = Path(__file__).resolve().parent
    if (package_dir.parent / ".git").exists() or (package_dir.parent / "pyproject.toml").exists():
        return "source"
    return "pip"


def check(force: bool = False, transport: Optional[httpx.BaseTransport] = None) -> Dict[str, Any]:
    """The newest release and whether it is newer than this version."""
    if not force and _cache["data"] and time.time() - _cache["at"] < CHECK_EVERY_SECONDS:
        return _cache["data"]
    current = parse_version(__version__)
    result: Dict[str, Any] = {
        "current": __version__, "latest": None, "newer": False, "notes": "", "url": None,
        "repo": repo(), "install": install_kind(), "error": None,
    }
    try:
        with httpx.Client(timeout=15, transport=transport, headers={"Accept": "application/vnd.github+json",
                                                                    "User-Agent": "agentd-update-check"}) as client:
            response = client.get(f"https://api.github.com/repos/{repo()}/releases/latest")
        if response.status_code == 404:
            result["error"] = "No releases have been published yet."
        elif response.status_code != 200:
            result["error"] = f"GitHub answered {response.status_code}."
        else:
            release = response.json()
            latest = parse_version(release.get("tag_name", ""))
            wheel = next((a["browser_download_url"] for a in release.get("assets", [])
                          if str(a.get("name", "")).endswith(".whl")), None)
            result.update(
                latest=str(latest) if latest else release.get("tag_name"),
                tag=release.get("tag_name"),
                newer=bool(latest and current and latest > current and not release.get("prerelease")),
                notes=(release.get("body") or "")[:4000],
                url=release.get("html_url"),
                wheel=wheel,
            )
    except httpx.HTTPError as exc:
        result["error"] = f"Could not reach GitHub ({type(exc).__name__})."
    _cache.update(at=time.time(), data=result)
    return result


def install(info: Dict[str, Any], runner=subprocess.run) -> Dict[str, Any]:
    """Install the release described by ``check()``. Returns what happened and whether to restart."""
    if not info.get("newer"):
        return {"ok": False, "message": "You already have the latest version."}
    kind = info.get("install")
    if kind == "source":
        return {"ok": False, "message": "This copy runs from source code. Update it with: git pull", "restart": False}
    if kind == "packaged":
        return {"ok": False, "open": info.get("url"), "message": "Download the new installer from the release page.", "restart": False}

    tag = str(info.get("tag") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        return {"ok": False, "message": "The release tag looks wrong, so nothing was installed."}
    # The wheel attached to the release if there is one, otherwise the tagged source
    target = info.get("wheel") or f"git+https://github.com/{repo()}.git@{tag}"
    if info.get("wheel") and not str(target).startswith(f"https://github.com/{repo()}/releases/download/"):
        return {"ok": False, "message": "The release file is not on this project's GitHub, so nothing was installed."}
    done = runner([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", target],
                  capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "")[-600:]
        return {"ok": False, "message": f"The update did not install:\n{tail}"}
    _cache.update(at=0.0, data=None)
    return {"ok": True, "restart": True, "message": f"Version {info.get('latest')} is installed. Restarting…"}
