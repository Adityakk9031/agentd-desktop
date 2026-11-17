"""Login vault — saved sign-ins the agent can use but never see.

The user saves a site, a username and a password once. When a page asks for
them, the agent calls ``browser_fill_login``: the vault types the values into
the page itself, and the agent only learns that it happened. The password
never enters the model's context, a log, or the chat.

A login is only filled on the site it was saved for (or a subdomain of it),
so a look-alike page cannot get it, whatever the agent was told.

Storage: one file encrypted with Fernet (AES-128-CBC + HMAC). The key is kept
in the operating system keychain when available (Windows Credential Manager,
macOS Keychain), otherwise in a file beside it readable only by this user.
"""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken


class VaultError(ValueError):
    """Bad input, or the vault file could not be read."""


def normalise_site(site: str) -> str:
    """'https://www.Amazon.in/login' -> 'amazon.in'."""
    text = (site or "").strip().lower()
    host = urlparse(text if "://" in text else "https://" + text).hostname or ""
    host = host[4:] if host.startswith("www.") else host
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        raise VaultError("Enter the website as a name like amazon.in or github.com.")
    return host


def site_matches(saved: str, page_url: str) -> bool:
    """True when the page is the saved site or one of its subdomains."""
    host = (urlparse(page_url).hostname or "").lower()
    return host == saved or host.endswith("." + saved)


KEYCHAIN_SERVICE = "agentd-vault"


def _keychain_key(folder: Path) -> Optional[bytes]:
    """The vault key from the operating system keychain (Windows Credential Manager, macOS Keychain).

    Used when the ``keyring`` package is installed and AGENTD_VAULT_KEYCHAIN is not "0". A key file
    from an earlier version is moved into the keychain and deleted. Returns None to fall back to a file.
    """
    if os.getenv("AGENTD_VAULT_KEYCHAIN", "1") in ("0", "false", "no"):
        return None
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    account = str(folder.resolve())
    try:
        stored = keyring.get_password(KEYCHAIN_SERVICE, account)
        if stored:
            return stored.encode("ascii")
        old = folder / "vault.key"
        key = old.read_bytes().strip() if old.exists() else Fernet.generate_key()
        keyring.set_password(KEYCHAIN_SERVICE, account, key.decode("ascii"))
        if keyring.get_password(KEYCHAIN_SERVICE, account) == key.decode("ascii") and old.exists():
            old.unlink()  # the keychain now holds it
        return key
    except (KeyringError, OSError, RuntimeError):
        return None


class Vault:
    def __init__(self, folder: Optional[str] = None):
        self.folder = Path(folder or os.getenv("AGENTD_VAULT_DIR") or Path("data") / "vault")
        self.data_file = self.folder / "logins.enc"
        self.key_file = self.folder / "vault.key"

    # ----------------------------------------------------------- storage

    def _fernet(self) -> Fernet:
        self.folder.mkdir(parents=True, exist_ok=True)
        key = _keychain_key(self.folder)
        if key:
            return Fernet(key)
        if not self.key_file.exists():
            self.key_file.write_bytes(Fernet.generate_key())
            try:
                os.chmod(self.key_file, 0o600)
            except OSError:  # Windows: the file sits in the user's own folder
                pass
        return Fernet(self.key_file.read_bytes())

    def _load(self) -> List[Dict[str, str]]:
        if not self.data_file.exists():
            return []
        try:
            return json.loads(self._fernet().decrypt(self.data_file.read_bytes()))
        except (InvalidToken, ValueError) as exc:
            raise VaultError("The saved logins could not be unlocked.") from exc

    def _save(self, entries: List[Dict[str, str]]) -> None:
        token = self._fernet().encrypt(json.dumps(entries).encode("utf-8"))
        temp = self.data_file.with_suffix(".tmp")
        temp.write_bytes(token)
        os.replace(temp, self.data_file)

    # ------------------------------------------------------------- public

    def list(self) -> List[Dict[str, str]]:
        """Saved logins without their passwords."""
        return [{"id": e["id"], "site": e["site"], "username": e["username"]} for e in self._load()]

    def add(self, site: str, username: str, password: str) -> Dict[str, str]:
        site = normalise_site(site)
        username = (username or "").strip()
        if not username or not password:
            raise VaultError("Enter both the username and the password.")
        entries = [e for e in self._load() if not (e["site"] == site and e["username"] == username)]
        entry = {"id": uuid.uuid4().hex[:12], "site": site, "username": username, "password": password}
        entries.append(entry)
        self._save(entries)
        return {"id": entry["id"], "site": site, "username": username}

    def delete(self, login_id: str) -> bool:
        entries = self._load()
        kept = [e for e in entries if e["id"] != login_id]
        if len(kept) == len(entries):
            return False
        self._save(kept)
        return True

    def find_for(self, page_url: str) -> Optional[Dict[str, str]]:
        """The login saved for this page's site (the most specific match), password included."""
        matches = [e for e in self._load() if site_matches(e["site"], page_url)]
        return max(matches, key=lambda e: len(e["site"])) if matches else None

    def secrets(self) -> List[str]:
        """Every saved password, so tool output can be scrubbed of them."""
        return [e["password"] for e in self._load() if len(e["password"]) >= 4]


def redact(text: str, secrets: List[str]) -> str:
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "«hidden»")
    return text
