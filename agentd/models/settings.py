"""Save a model choice and its API key from the app's settings screen.

Keys are written to the local .env file (never returned by any API) and
applied to the running process, so no restart is needed. Moving them to the
operating system keychain is planned.
"""

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from dotenv import find_dotenv, set_key

from agentd.models import config as model_config
from agentd.models.config import PROVIDERS, ModelConfigError, ModelSpec, load_models_config, parse_spec


def env_file_path() -> Path:
    """The .env used by this install (created next to the working directory if missing)."""
    found = find_dotenv(usecwd=True)
    return Path(found) if found else Path.cwd() / ".env"


def ping_model(spec: ModelSpec) -> str:
    """Send one tiny request; returns the reply text or raises."""
    from agentd.models.client import message_text
    from agentd.models.factory import build_chat_model

    reply = build_chat_model(spec).invoke("Reply with the single word: pong")
    return message_text(reply).strip()


def save_model_setting(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    env_path: Optional[Path] = None,
    ping: Callable[[ModelSpec], str] = ping_model,
    test: bool = True,
) -> Dict[str, Any]:
    """
    Make ``provider:model`` the default model, keeping the previous models as
    fallbacks. Stores ``api_key`` when given. The model is tested first; a
    model that does not answer is NOT saved as the default.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    api_key = (api_key or "").strip()
    spec = parse_spec(f"{provider}:{model}")
    info = PROVIDERS.get(provider)
    if info is None:
        raise ModelConfigError(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")

    key_env = info.api_key_env
    previous_key = os.environ.get(key_env) if key_env else None
    if api_key and key_env:
        os.environ[key_env] = api_key  # needed for the test request below
    if spec.needs_key() and not spec.api_key():
        raise ModelConfigError(f"{provider} needs an API key.")

    try:
        # test=False is the quick switch between models whose key already works
        reply = ping(spec) if test else "(not tested)"
    except Exception as exc:
        if key_env:  # put the old key back; nothing was saved
            if previous_key is None:
                os.environ.pop(key_env, None)
            else:
                os.environ[key_env] = previous_key
        return {"ok": False, "model": spec.id, "message": _friendly_error(exc)}

    # Previous models become fallbacks, in their existing order
    fallbacks = [s.id for s in load_models_config().chain("worker") if s.id != spec.id]

    path = Path(env_path) if env_path else env_file_path()
    path.touch(exist_ok=True)
    updates = {"AGENTD_MODEL_DEFAULT": spec.id, "AGENTD_MODEL_DEFAULT_FALLBACKS": ",".join(fallbacks)}
    if api_key and key_env:
        updates[key_env] = api_key
    for name, value in updates.items():
        set_key(str(path), name, value, quote_mode="never")
        os.environ[name] = value

    model_config.bump_config_version()
    from agentd.models.catalog import clear_catalog_cache
    clear_catalog_cache()  # a new key changes which lists we can fetch
    message = f"{spec.id} answered: {reply[:40]!r}" if test else f"Now using {spec.id}"
    return {"ok": True, "model": spec.id, "fallbacks": fallbacks, "message": message}


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "expired" in lowered:
        return "This API key has expired. Create a new key and try again."
    if "401" in text or "invalid api key" in lowered or "incorrect api key" in lowered or "unauthorized" in lowered:
        return "The provider rejected this API key. Check that it was copied completely."
    if "quota" in lowered or "insufficient" in lowered or "billing" in lowered:
        return "The key is valid but has no credit or quota left."
    if "404" in text or "not found" in lowered or "does not exist" in lowered:
        return "The provider does not know this model name. Check the spelling."
    if "pip install" in lowered:
        return text
    if "connect" in lowered:
        return "Could not reach the provider. Check the internet connection (or that Ollama is running)."
    return f"{type(exc).__name__}: {text[:200]}"


def save_provider_key(
    provider: str,
    api_key: str,
    env_path: Optional[Path] = None,
    lister: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """
    Check an API key with its provider and, if accepted, store it.

    Returns the models that key can use. A rejected key is not stored and
    raises ModelListError with the provider's reason.
    """
    from agentd.models.catalog import clear_catalog_cache, list_models

    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    info = PROVIDERS.get(provider)
    if info is None or not info.api_key_env:
        raise ModelConfigError(f"'{provider}' does not use an API key.")
    if not api_key:
        raise ModelConfigError("Enter an API key.")

    models = (lister or list_models)(provider, api_key)  # raises if the provider rejects the key

    path = Path(env_path) if env_path else env_file_path()
    path.touch(exist_ok=True)
    set_key(str(path), info.api_key_env, api_key, quote_mode="never")
    os.environ[info.api_key_env] = api_key
    model_config.bump_config_version()
    clear_catalog_cache()
    return {"ok": True, "provider": provider, "models": models}
