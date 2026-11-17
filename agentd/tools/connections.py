"""Your own MCP servers — connect the agent to any external tool.

MCP (Model Context Protocol) is how an agent borrows tools from another
program. Add a server once (a local command, or a web address) and its tools
are offered to the agent next to the built-in ones, the way OpenClaw does it.

Safety: a tool the server marks read-only runs on its own; everything else
asks first (see agentd.runtime.agent). Saved in data/mcp_servers.json on
this computer; values such as tokens in ``env`` or ``headers`` stay local.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RESERVED = {"agentd", "builtin"}


class ConnectionError_(ValueError):
    """Invalid connection settings."""


def store_path() -> Path:
    return Path(os.getenv("AGENTD_MCP_SERVERS") or Path("data") / "mcp_servers.json")


def load() -> Dict[str, Dict[str, Any]]:
    path = store_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(servers: Dict[str, Dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(servers, indent=2), encoding="utf-8")
    os.replace(temp, path)


def validate(name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Clean settings for one server, or ConnectionError_ saying what is wrong."""
    name = (name or "").strip().lower()
    if not NAME.match(name) or name in RESERVED:
        raise ConnectionError_("Name it with lowercase letters, numbers, - or _ (for example: notion).")
    transport = (config.get("transport") or "").strip().lower()
    if transport == "stdio":
        command = (config.get("command") or "").strip()
        if not command:
            raise ConnectionError_("Enter the command that starts the server (for example: npx).")
        args = config.get("args") or []
        if isinstance(args, str):
            args = args.split()
        env = config.get("env") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ConnectionError_("Environment values must be text.")
        return {"transport": "stdio", "command": command, "args": [str(a) for a in args], "env": env}
    if transport in ("http", "streamable_http", "sse"):
        url = (config.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise ConnectionError_("Enter the server's web address, starting with https://")
        headers = config.get("headers") or {}
        if not isinstance(headers, dict):
            raise ConnectionError_("Headers must be name: value pairs.")
        return {"transport": "sse" if transport == "sse" else "streamable_http", "url": url,
                "headers": {str(k): str(v) for k, v in headers.items()}}
    raise ConnectionError_("Transport must be stdio (a local command) or http (a web address).")


def add(name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    clean = validate(name, config)
    servers = load()
    servers[name.strip().lower()] = clean
    _save(servers)
    return {"name": name.strip().lower(), **public_view(clean)}


def remove(name: str) -> bool:
    servers = load()
    if name not in servers:
        return False
    del servers[name]
    _save(servers)
    return True


def public_view(config: Dict[str, Any]) -> Dict[str, Any]:
    """Settings safe to show in the app: environment and header values are hidden."""
    shown = {k: v for k, v in config.items() if k not in ("env", "headers")}
    if config.get("env"):
        shown["env"] = sorted(config["env"])
    if config.get("headers"):
        shown["headers"] = sorted(config["headers"])
    return shown


def listing() -> List[Dict[str, Any]]:
    return [{"name": name, **public_view(config)} for name, config in sorted(load().items())]


def connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """The connection dict langchain-mcp-adapters expects."""
    if config["transport"] == "stdio":
        env = {**os.environ, **config.get("env", {})} if config.get("env") else None
        return {"transport": "stdio", "command": config["command"], "args": config.get("args", []), "env": env}
    return {"transport": config["transport"], "url": config["url"], "headers": config.get("headers") or None}
