"""Hard rules for computer tools. They apply in every mode, before any approval.

These are guard rails, not a sandbox: a command the user approves runs with
the user's own rights. The rules stop the well-known ways an agent wrecks a
machine or leaks secrets, and they cannot be switched off by the model.
"""

import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse


class Blocked(Exception):
    """The action is never allowed. The message is shown to the agent and the user."""


# Folders and files that hold credentials
_SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", ".password-store"}
_SECRET_FILE = re.compile(
    r"(^\.env(\..*)?$|^id_(rsa|dsa|ecdsa|ed25519)|\.pem$|\.key$|\.pfx$|\.p12$|\.kdbx$|^credentials(\..*)?$|"
    r"^login data|^cookies$|^web data$|^key[34]\.db$|^logins\.json$|^\.netrc$|^\.npmrc$|^\.pypirc$)",
    re.IGNORECASE,
)
_BROWSER_PROFILE = re.compile(r"(google[\\/]chrome|microsoft[\\/]edge|bravesoftware|mozilla[\\/]firefox)[\\/]", re.IGNORECASE)
_SYSTEM_WRITE = re.compile(r"^(c:[\\/](windows|program files|program files \(x86\)|programdata)|/(etc|usr|bin|sbin|boot|sys|proc|dev|system|library)(/|$))", re.IGNORECASE)

# Commands that are destructive, change the system, or dig for secrets
_BAD_COMMANDS = [
    (r"\bformat(\.com)?\s+[a-z]:", "formatting a drive"),
    (r"\b(diskpart|bcdedit|mkfs\w*|fdisk)\b", "disk and boot tools"),
    (r"\bdd\b.*\bof=/dev/", "writing to a raw disk"),
    (r"\brm\s+(-\w+\s+)*-\w*[rf]\w*\s+(-\w+\s+)*(/|~|\$home|[a-z]:[\\/]?)(\s|$|\*)", "deleting a whole drive or home folder"),
    (r"\b(remove-item|rmdir|rd|del|erase)\b[^|;&]*\s([a-z]:[\\/]?|~|\$home|\$env:userprofile|%userprofile%)\s*(\\\*|/\*)?(\s|$)", "deleting a whole drive or home folder"),
    (r"\b(shutdown|restart-computer|stop-computer|reboot|halt|poweroff)\b", "shutting down or restarting"),
    (r"\breg(\.exe)?\s+(delete|add)\b|\b(set|remove|new)-itemproperty\b.*\bhk(lm|cu):", "editing the registry"),
    (r"\b(net\s+user|net\s+localgroup|new-localuser|add-localgroupmember|passwd|useradd|usermod)\b", "changing user accounts"),
    (r"\b(set-executionpolicy|set-mppreference|netsh\s+advfirewall|ufw\s+disable)\b", "changing security settings"),
    (r"\b(cipher\s+/w|sdelete|shred)\b", "secure-wiping data"),
    (r":\(\)\s*\{", "a fork bomb"),
    (r"(\.ssh[\\/]|id_rsa|id_ed25519|\.aws[\\/]credentials|\.env\b|login data|\bcookies\b.*(chrome|edge|firefox))", "reading stored secrets"),
    (r"\b(invoke-expression|iex)\b.*\b(downloadstring|invoke-webrequest|iwr|curl|wget)\b|\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh|python\d?|powershell|pwsh)\b", "running code downloaded from the internet"),
]
_BAD = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in _BAD_COMMANDS]


def workspace() -> Path:
    """Where relative paths resolve and commands start. Default: the user's home folder."""
    return Path(os.getenv("AGENTD_WORKSPACE") or Path.home()).expanduser().resolve()


def resolve_path(path: str, writing: bool = False) -> Path:
    """Absolute path for a tool argument, or Blocked if it must never be touched."""
    raw = Path(os.path.expandvars(str(path or "."))).expanduser()
    full = (raw if raw.is_absolute() else workspace() / raw).resolve()
    parts = [p.lower() for p in full.parts]
    if _SECRET_DIRS.intersection(parts) or _SECRET_FILE.search(full.name) or _BROWSER_PROFILE.search(str(full)):
        raise Blocked(f"'{full.name}' is in a protected location that holds passwords or keys. The agent may not open it.")
    if writing and _SYSTEM_WRITE.search(str(full)):
        raise Blocked(f"'{full}' is a system folder. The agent may not change files there.")
    return full


def check_command(command: str) -> None:
    """Raise Blocked for commands that are never allowed."""
    for pattern, why in _BAD:
        if pattern.search(command or ""):
            raise Blocked(f"This command is blocked because it involves {why}.")


def check_url(url: str) -> str:
    """Only public http(s) addresses: the agent must not reach this computer's own services."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Blocked("Only http and https web addresses can be fetched.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror:
        raise Blocked(f"Could not find the website '{parsed.hostname}'.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise Blocked("That address is on this computer or the local network, which the agent may not fetch.")
    return parsed.geturl()
