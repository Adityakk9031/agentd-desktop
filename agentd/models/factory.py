"""Build LangChain chat and embedding models from a ModelSpec."""

from typing import Any, Dict

from agentd.models.config import ModelConfigError, ModelSpec, current_effort

# What each provider calls the effort setting
_EFFORT_ARGUMENT = {
    "openai": "reasoning_effort", "xai": "reasoning_effort", "anthropic": "reasoning_effort",
    "google_genai": "thinking_level", "openrouter": "reasoning_effort", "groq": "reasoning_effort",
    "deepseek": "reasoning_effort",
}


def effort_argument(spec: ModelSpec):
    """(argument name, value) for the effort in force, or None when it does not apply."""
    effort = current_effort()
    name = _EFFORT_ARGUMENT.get(spec.provider)
    return (name, effort) if effort and name and name not in spec.params else None


def is_effort_error(exc: Exception) -> bool:
    """True when a model refused the request because it does not take an effort setting."""
    text = str(exc).lower()
    return any(word in text for word in ("reasoning_effort", "reasoning effort", "thinking_level", "thinking level", "thinking is not", "does not support reasoning", "reasoningeffort"))


class MissingProviderPackage(ModelConfigError):
    """The integration package for a provider is not installed."""


# Providers whose LangChain classes accept `max_retries` and `timeout`.
_RETRY_AWARE = {"openai", "anthropic", "google_genai", "xai", "groq", "mistralai", "deepseek", "openrouter"}

# Provider SDKs retry failed requests themselves (Google's up to 6 times with
# growing waits). That delays switching to the next model in a fallback
# chain, so keep their retries short. Override per model with `params`.
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 120


def _kwargs(spec: ModelSpec, embeddings: bool = False) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if not embeddings and spec.provider in _RETRY_AWARE:
        kwargs["max_retries"] = DEFAULT_MAX_RETRIES
        kwargs["timeout"] = DEFAULT_TIMEOUT_SECONDS
    if not embeddings:
        effort = effort_argument(spec)
        if effort:
            kwargs[effort[0]] = effort[1]
    kwargs.update(spec.params)
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if not embeddings and spec.temperature is not None:
        kwargs["temperature"] = spec.temperature
    key = spec.api_key()
    if key:
        info = spec.info
        kwargs[info.key_kwarg if info else "api_key"] = key
    if embeddings and spec.dimensions:
        # Each provider names this setting differently
        dim_arg = {"google_genai": "output_dimensionality", "openai": "dimensions"}.get(spec.provider)
        if dim_arg:
            kwargs[dim_arg] = spec.dimensions
    return kwargs


def _missing_package_error(spec: ModelSpec, exc: ImportError) -> MissingProviderPackage:
    package = spec.info.package if spec.info else f"the LangChain integration for '{spec.provider}'"
    return MissingProviderPackage(
        f"Model {spec.id} needs {package}. Install it with: pip install {package}  ({exc})"
    )


def build_chat_model(spec: ModelSpec):
    """Create a chat model; raises MissingProviderPackage / ModelConfigError."""
    from langchain.chat_models import init_chat_model

    if spec.needs_key() and not spec.api_key():
        raise ModelConfigError(
            f"Model {spec.id} needs an API key in {' or '.join(spec.key_envs())}"
        )
    try:
        return init_chat_model(spec.model, model_provider=spec.provider, **_kwargs(spec))
    except ImportError as exc:
        raise _missing_package_error(spec, exc) from exc
    except ValueError as exc:
        raise ModelConfigError(f"Cannot create model {spec.id}: {exc}") from exc


def build_embeddings(spec: ModelSpec):
    """Create an embeddings model; raises MissingProviderPackage / ModelConfigError."""
    from langchain.embeddings import init_embeddings

    if spec.needs_key() and not spec.api_key():
        raise ModelConfigError(
            f"Embeddings {spec.id} need an API key in {' or '.join(spec.key_envs())}"
        )
    try:
        return init_embeddings(spec.model, provider=spec.provider, **_kwargs(spec, embeddings=True))
    except ImportError as exc:
        raise _missing_package_error(spec, exc) from exc
    except ValueError as exc:
        raise ModelConfigError(f"Cannot create embeddings {spec.id}: {exc}") from exc
