"""Ask a provider which models an API key can use.

Each provider has an official "list models" endpoint. The key is sent only
to that provider's own API address.
"""

from typing import Any, Dict, List, Optional

import httpx

from agentd.models.config import PROVIDERS, ModelConfigError, parse_spec

# OpenAI-style: GET <base>/models with a Bearer key -> {"data": [{"id": ...}]}
_OPENAI_STYLE = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com",
    "mistralai": "https://api.mistral.ai/v1",
}

# Names that are not chat models (OpenAI lists everything together)
_NOT_CHAT = (
    "embedding", "embed", "whisper", "tts", "dall-e", "moderation", "image", "audio",
    "realtime", "transcribe", "sora", "babbage", "davinci", "computer-use", "search", "codex",
)


class ModelListError(ModelConfigError):
    """The provider refused or could not be reached."""


def _fail(provider: str, response: httpx.Response) -> ModelListError:
    try:
        body = response.json()
        error = body.get("error", body)
        detail = error.get("message") if isinstance(error, dict) else str(error)
    except Exception:
        detail = response.text[:200]
    if response.status_code in (401, 403):
        return ModelListError(f"{provider} rejected this API key: {detail or 'not authorised'}")
    return ModelListError(f"{provider} returned an error ({response.status_code}): {detail}")


def list_models(
    provider: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    transport: Optional[httpx.BaseTransport] = None,
) -> List[Dict[str, Any]]:
    """
    Return the chat models available to this key, newest first where the
    provider reports dates: ``[{"id": "gpt-5.4", "label": "gpt-5.4"}, ...]``.
    Uses the saved key for the provider when ``api_key`` is empty.
    """
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ModelConfigError(f"Unknown provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
    key = (api_key or "").strip() or parse_spec(f"{provider}:x").api_key()
    info = PROVIDERS[provider]
    if info.api_key_env and not key and provider != "openrouter":
        raise ModelConfigError(f"{provider} needs an API key to list its models.")

    try:
        # A local Ollama either answers at once or is not running
        timeout = httpx.Timeout(20, connect=2 if provider == "ollama" else 10)
        with httpx.Client(timeout=timeout, transport=transport) as client:
            if provider in _OPENAI_STYLE:
                models = _openai_style(client, provider, base_url or _OPENAI_STYLE[provider], key)
            elif provider == "anthropic":
                models = _anthropic(client, base_url or "https://api.anthropic.com", key)
            elif provider == "google_genai":
                models = _google(client, base_url or "https://generativelanguage.googleapis.com", key)
            else:  # ollama
                models = _ollama(client, base_url or "http://localhost:11434")
    except httpx.HTTPError as exc:
        hint = " Is Ollama running?" if provider == "ollama" else ""
        raise ModelListError(f"Could not reach {provider}: {type(exc).__name__}.{hint}") from exc

    seen, unique = set(), []
    for model in models:
        if model["id"] and model["id"] not in seen:
            seen.add(model["id"])
            unique.append(model)
    return unique


def _openai_style(client: httpx.Client, provider: str, base: str, key: Optional[str]) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = client.get(f"{base.rstrip('/')}/models", headers=headers)
    if response.status_code != 200:
        raise _fail(provider, response)
    data = response.json().get("data", [])
    if provider == "openai":
        data = [m for m in data if not any(word in str(m.get("id", "")).lower() for word in _NOT_CHAT)]
    if provider == "xai":
        data = [m for m in data if "imagine" not in str(m.get("id", "")).lower()]
    data.sort(key=lambda m: m.get("created") or 0, reverse=True)
    return [{"id": str(m.get("id", "")), "label": str(m.get("name") or m.get("id", ""))} for m in data]


def _anthropic(client: httpx.Client, base: str, key: str) -> List[Dict[str, Any]]:
    response = client.get(
        f"{base.rstrip('/')}/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        params={"limit": 100},
    )
    if response.status_code != 200:
        raise _fail("anthropic", response)
    return [
        {"id": str(m.get("id", "")), "label": str(m.get("display_name") or m.get("id", ""))}
        for m in response.json().get("data", [])
    ]


def _google(client: httpx.Client, base: str, key: str) -> List[Dict[str, Any]]:
    response = client.get(
        f"{base.rstrip('/')}/v1beta/models",
        headers={"x-goog-api-key": key},  # header, so the key never appears in a URL or log
        params={"pageSize": 200},
    )
    if response.status_code != 200:
        raise _fail("google_genai", response)
    models = [
        m for m in response.json().get("models", [])
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
    ]
    skip = ("image", "tts", "lyria", "veo", "imagen", "banana", "audio", "embedding", "aqa", "robotics", "live")
    models = [m for m in models if not any(word in str(m.get("name", "")).lower() for word in skip)]
    # Gemini models first, newest version numbers on top
    models.sort(key=lambda m: str(m.get("name", "")), reverse=True)
    models.sort(key=lambda m: "gemini" not in str(m.get("name", "")))
    return [
        {"id": str(m.get("name", "")).removeprefix("models/"), "label": str(m.get("displayName") or m.get("name", ""))}
        for m in models
    ]


def _ollama(client: httpx.Client, base: str) -> List[Dict[str, Any]]:
    response = client.get(f"{base.rstrip('/')}/api/tags")
    if response.status_code != 200:
        raise _fail("ollama", response)
    return [{"id": str(m.get("name", "")), "label": str(m.get("name", ""))} for m in response.json().get("models", [])]


# ------------------------------------------------------------ public catalog
#
# The latest models of every provider, without needing any key. OpenRouter
# publishes one public list covering all the big providers; we regroup it by
# provider and convert the names to each provider's own model ids.

PUBLIC_LIST_URL = "https://openrouter.ai/api/v1/models"
_PUBLIC_PREFIX = {
    "openai": "openai", "anthropic": "anthropic", "google": "google_genai", "x-ai": "xai",
    "deepseek": "deepseek", "mistralai": "mistralai",
}
PROVIDER_NAMES = {
    "openai": "OpenAI", "anthropic": "Anthropic (Claude)", "google_genai": "Google (Gemini)", "xai": "xAI (Grok)",
    "groq": "Groq", "openrouter": "OpenRouter (all providers, one key)", "deepseek": "DeepSeek",
    "mistralai": "Mistral", "ollama": "Ollama (local, free)",
}
MAX_PER_PROVIDER = 30
_cache: Dict[str, Any] = {"at": 0.0, "data": None}
CACHE_SECONDS = 1800


def _native_id(provider: str, name: str) -> str:
    """Convert a public-list name to the id the provider's own API expects."""
    if provider == "anthropic":
        import re
        return re.sub(r"(?<=\d)\.(?=\d)", "-", name)  # claude-sonnet-5.5 -> claude-sonnet-5-5
    return name


def public_models(transport: Optional[httpx.BaseTransport] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Latest chat models per provider from the public list, newest first."""
    with httpx.Client(timeout=20, transport=transport) as client:
        response = client.get(PUBLIC_LIST_URL)
    if response.status_code != 200:
        raise ModelListError(f"Public model list returned {response.status_code}")
    grouped: Dict[str, List[Dict[str, Any]]] = {p: [] for p in PROVIDERS}
    data = sorted(response.json().get("data", []), key=lambda m: m.get("created") or 0, reverse=True)
    for model in data:
        full_id = str(model.get("id", ""))
        if ":" in full_id or "/" not in full_id:  # skip :batch / :free variants
            continue
        outputs = (model.get("architecture") or {}).get("output_modalities") or ["text"]
        if "text" not in outputs:
            continue
        prefix, _, name = full_id.partition("/")
        label = str(model.get("name") or name).split(": ", 1)[-1]
        if len(grouped["openrouter"]) < 80:
            grouped["openrouter"].append({"id": full_id, "label": str(model.get("name") or full_id)})
        provider = _PUBLIC_PREFIX.get(prefix)
        if provider and len(grouped[provider]) < MAX_PER_PROVIDER:
            grouped[provider].append({"id": _native_id(provider, name), "label": label})
    return grouped


def full_catalog(refresh: bool = False, transport: Optional[httpx.BaseTransport] = None) -> List[Dict[str, Any]]:
    """
    Every provider with its latest models, for the model picker.

    Where a key is already saved, the provider's own list is used (exact for
    that key). Otherwise the public list is used, so models can be browsed
    before any key is entered.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    if not refresh and _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]

    try:
        public = public_models(transport)
    except Exception:
        public = {p: [] for p in PROVIDERS}

    def own(provider: str):
        has_key = bool(parse_spec(f"{provider}:x").api_key())
        if has_key or provider == "ollama":
            try:
                models = list_models(provider, transport=transport)
                if models:
                    return provider, models[:60], "provider", has_key
            except Exception:
                pass
        models = public.get(provider) or []
        return provider, models, ("public" if models else "none"), has_key

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(own, PROVIDERS))
    data = [
        {
            "id": provider, "name": PROVIDER_NAMES.get(provider, provider),
            "needs_key": bool(PROVIDERS[provider].api_key_env), "has_key": has_key,
            "models": models, "source": source,
        }
        for provider, models, source, has_key in rows
    ]
    _cache.update(at=time.time(), data=data)
    return data


def clear_catalog_cache() -> None:
    _cache.update(at=0.0, data=None)
