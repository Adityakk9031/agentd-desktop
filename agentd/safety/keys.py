"""API keys in the operating system's keychain (Windows Credential Manager, macOS Keychain).

A key entered in the app is checked with its provider, then stored in the
keychain under the service "agentd-keys", one entry per key name (for example
OPENAI_API_KEY). It is not written to the .env file, and an older copy of the
same key in .env is removed, so the key is never left in a plain text file.

At start-up every stored key is put into this process's environment, which is
how the model code reads keys. A key set in the environment or .env by hand
still works and wins, so developers and servers can keep using .env.

Set AGENTD_KEYCHAIN=0 to keep keys in .env instead (also used when no keychain
is available, for example on a Linux server without a keyring).
"""

import logging
import os
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger("agentd.keys")

SERVICE = "agentd-keys"


def keychain_enabled() -> bool:
    if os.getenv("AGENTD_KEYCHAIN", "1") in ("0", "false", "no"):
        return False
    try:
        import keyring  # noqa: F401
    except ImportError:
        return False
    return True


def key_names() -> List[str]:
    """Every key name a provider can use."""
    from agentd.models.config import PROVIDERS

    names = []
    for info in PROVIDERS.values():
        for name in (info.api_key_env, *info.extra_key_envs):
            if name and name not in names:
                names.append(name)
    return names


def store_key(name: str, value: str, env_path: Optional[Path] = None) -> str:
    """Store a key; returns "keychain" or "env-file". Always also sets it for this process."""
    os.environ[name] = value
    if keychain_enabled():
        try:
            import keyring

            keyring.set_password(SERVICE, name, value)
            if keyring.get_password(SERVICE, name) == value:
                _remove_from_env_file(name, env_path)
                return "keychain"
        except Exception as exc:  # no usable keychain: fall back to the file
            logger.warning(f"Could not use the keychain for {name}; saving it in .env instead: {type(exc).__name__}")
    from dotenv import set_key

    path = Path(env_path) if env_path else _env_file()
    path.touch(exist_ok=True)
    set_key(str(path), name, value, quote_mode="never")
    return "env-file"


def load_keys(names: Optional[Iterable[str]] = None) -> List[str]:
    """Put keys from the keychain into the environment, unless already set there. Returns the names loaded."""
    if not keychain_enabled():
        return []
    loaded = []
    try:
        import keyring

        for name in names or key_names():
            if os.environ.get(name):
                continue
            value = keyring.get_password(SERVICE, name)
            if value:
                os.environ[name] = value
                loaded.append(name)
    except Exception as exc:
        logger.warning(f"Could not read keys from the keychain: {type(exc).__name__}")
    return loaded


def delete_key(name: str) -> None:
    os.environ.pop(name, None)
    if keychain_enabled():
        try:
            import keyring

            keyring.delete_password(SERVICE, name)
        except Exception:
            pass


def _env_file() -> Path:
    from agentd.models.settings import env_file_path

    return env_file_path()


def _remove_from_env_file(name: str, env_path: Optional[Path]) -> None:
    path = Path(env_path) if env_path else _env_file()
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if not line.lstrip().startswith((f"{name}=", f"export {name}="))]
    if len(kept) != len(lines):
        path.write_text("".join(kept), encoding="utf-8")
