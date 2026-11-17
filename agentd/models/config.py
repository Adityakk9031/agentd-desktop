"""Model configuration — which model does which job.

Roles
-----
- ``planner``:    turns a task into a plan
- ``worker``:     writes direct answers and final summaries
- ``verifier``:   optional AI check of results
- ``embeddings``: turns text into vectors for memory

Every role falls back to ``default`` when it is not set. Each role has a
primary model plus an ordered list of fallbacks: when a model fails (quota
used up, bad key, provider down) the next one is used automatically.

Models are written ``provider:model``, e.g. ``openai:gpt-5.4``,
``anthropic:claude-sonnet-5-5``, ``google_genai:gemini-3.5-flash``,
``ollama:llama3.1``, ``openrouter:meta-llama/llama-4-maverick``.

Sources, lowest to highest priority
-----------------------------------
1. Legacy: ``GEMINI_API_KEY`` (+ ``GEMINI_MODEL``) means Gemini is the default.
2. ``agentd.yaml`` in the working directory (or the path in ``AGENTD_CONFIG``).
3. Environment variables::

       AGENTD_MODEL_DEFAULT=openai:gpt-5.4-mini
       AGENTD_MODEL_PLANNER=anthropic:claude-sonnet-5-5
       AGENTD_MODEL_PLANNER_FALLBACKS=google_genai:gemini-3.5-flash,ollama:llama3.1
       AGENTD_MODEL_EMBEDDINGS=openai:text-embedding-3-small
"""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

ROLES = ("planner", "worker", "verifier", "embeddings")
CHAT_ROLES = ("planner", "worker", "verifier")
DEFAULT_CONFIG_FILE = "agentd.yaml"


@dataclass(frozen=True)
class ProviderInfo:
    package: str                      # pip package that provides the integration
    api_key_env: Optional[str]        # usual env var holding the key (None = no key)
    extra_key_envs: tuple = ()        # other env vars accepted for the same key
    key_kwarg: str = "api_key"        # constructor argument for an explicit key


# Providers we document and check in `agentd doctor`. Any other provider that
# LangChain's init_chat_model supports still works; doctor just knows less.
PROVIDERS: Dict[str, ProviderInfo] = {
    "openai": ProviderInfo("langchain-openai", "OPENAI_API_KEY"),
    "anthropic": ProviderInfo("langchain-anthropic", "ANTHROPIC_API_KEY"),
    "google_genai": ProviderInfo("langchain-google-genai", "GOOGLE_API_KEY", ("GEMINI_API_KEY",), "google_api_key"),
    "ollama": ProviderInfo("langchain-ollama", None),
    "openrouter": ProviderInfo("langchain-openrouter", "OPENROUTER_API_KEY"),
    "xai": ProviderInfo("langchain-xai", "XAI_API_KEY"),
    "groq": ProviderInfo("langchain-groq", "GROQ_API_KEY"),
    "mistralai": ProviderInfo("langchain-mistralai", "MISTRAL_API_KEY"),
    "deepseek": ProviderInfo("langchain-deepseek", "DEEPSEEK_API_KEY"),
}


# A run (for example one automation) can use its own model without changing
# the saved default. The override is local to that run's task.
import contextvars

_model_override: contextvars.ContextVar = contextvars.ContextVar("agentd_model_override", default=None)


def set_model_override(model_id: Optional[str]):
    """Use ``provider:model`` first for chat roles in the current task. Returns a reset token."""
    return _model_override.set(model_id or None)


def reset_model_override(token) -> None:
    _model_override.reset(token)


# Effort: how hard the model thinks before answering. Lower effort is
# faster and uses fewer tokens; higher effort is better on hard tasks.
EFFORTS = ("low", "medium", "high")
_effort: contextvars.ContextVar = contextvars.ContextVar("agentd_effort", default="unset")


def set_effort(level: Optional[str]):
    """Set the effort for the current task ("low" | "medium" | "high", or None for the model's default)."""
    level = (level or "").strip().lower()
    return _effort.set(level if level in EFFORTS else None)


def reset_effort(token) -> None:
    _effort.reset(token)


def current_effort() -> Optional[str]:
    """The effort in force: this task's choice, else AGENTD_EFFORT, else the model's own default."""
    chosen = _effort.get()
    if chosen == "unset":
        chosen = (os.getenv("AGENTD_EFFORT") or "").strip().lower()
    return chosen if chosen in EFFORTS else None


# Bumped whenever settings change at runtime, so clients reload their models.
_config_version = 0


def config_version() -> int:
    return _config_version


def bump_config_version() -> None:
    global _config_version
    _config_version += 1


class ModelConfigError(ValueError):
    """Raised for invalid model configuration or when no model is configured."""


@dataclass(frozen=True)
class ModelSpec:
    """One model: ``provider:model`` plus optional connection settings."""

    provider: str
    model: str
    base_url: Optional[str] = None        # OpenAI-compatible servers, remote Ollama, proxies
    api_key_env: Optional[str] = None     # read the key from this env var instead of the default
    temperature: Optional[float] = None
    dimensions: Optional[int] = None      # embeddings only
    params: Dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def info(self) -> Optional[ProviderInfo]:
        return PROVIDERS.get(self.provider)

    def key_envs(self) -> List[str]:
        if self.api_key_env:
            return [self.api_key_env]
        info = self.info
        if info is None or info.api_key_env is None:
            return []
        return [info.api_key_env, *info.extra_key_envs]

    def api_key(self) -> Optional[str]:
        for name in self.key_envs():
            value = os.getenv(name)
            if value:
                return value
        return None

    def needs_key(self) -> bool:
        if self.api_key_env:
            return True
        info = self.info
        return bool(info and info.api_key_env)


def parse_spec(value: Any) -> ModelSpec:
    """Build a ModelSpec from ``"provider:model"`` or a mapping with ``model:``."""
    options: Dict[str, Any] = {}
    if isinstance(value, dict):
        options = dict(value)
        value = options.pop("model", None)
    if not isinstance(value, str) or ":" not in value.strip():
        raise ModelConfigError(
            f"Model {value!r} must be written as 'provider:model', e.g. 'openai:gpt-5.4-mini'"
        )
    provider, _, model = value.strip().partition(":")
    provider, model = provider.strip().lower(), model.strip()
    if not provider or not model:
        raise ModelConfigError(f"Model {value!r} must be written as 'provider:model'")
    known = {"base_url", "api_key_env", "temperature", "dimensions", "fallbacks"}
    options.pop("fallbacks", None)
    return ModelSpec(
        provider=provider,
        model=model,
        base_url=options.pop("base_url", None),
        api_key_env=options.pop("api_key_env", None),
        temperature=options.pop("temperature", None),
        dimensions=options.pop("dimensions", None),
        params={k: v for k, v in options.items() if k not in known},
    )


@dataclass
class RoleConfig:
    primary: ModelSpec
    fallbacks: List[ModelSpec] = field(default_factory=list)

    @property
    def chain(self) -> List[ModelSpec]:
        return [self.primary, *self.fallbacks]


@dataclass
class ModelsConfig:
    roles: Dict[str, RoleConfig] = field(default_factory=dict)
    source: str = "none"

    def chain(self, role: str) -> List[ModelSpec]:
        """Ordered models for a role (role first, then ``default``)."""
        cfg = self.roles.get(role) or (self.roles.get("default") if role != "embeddings" else None)
        chain = cfg.chain if cfg else []
        override = _model_override.get()
        if override and role != "embeddings":
            first = parse_spec(override)
            return [first] + [spec for spec in chain if spec.id != first.id]  # the rest stay as fallbacks
        return chain

    def describe(self) -> Dict[str, List[str]]:
        return {role: [s.id for s in self.chain(role)] for role in ROLES}


def _role_from_value(value: Any) -> RoleConfig:
    fallbacks_raw = value.get("fallbacks", []) if isinstance(value, dict) else []
    if isinstance(fallbacks_raw, str):
        fallbacks_raw = [f for f in fallbacks_raw.split(",") if f.strip()]
    return RoleConfig(parse_spec(value), [parse_spec(f) for f in fallbacks_raw])


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModelConfigError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise ModelConfigError(f"{path}: top level must be a mapping")
    return data


def _apply_provider_defaults(spec: ModelSpec, providers: Dict[str, Any]) -> ModelSpec:
    """Fill base_url / api_key_env from a ``providers:`` block when the model has none."""
    defaults = providers.get(spec.provider) or {}
    if not isinstance(defaults, dict):
        return spec
    return replace(
        spec,
        base_url=spec.base_url or defaults.get("base_url"),
        api_key_env=spec.api_key_env or defaults.get("api_key_env"),
    )


def load_models_config(config_path: Optional[str] = None) -> ModelsConfig:
    """Resolve the model configuration from legacy env, YAML and env overrides."""
    roles: Dict[str, RoleConfig] = {}
    sources: List[str] = []

    # 1. Legacy Gemini setup keeps working unchanged
    if os.getenv("GEMINI_API_KEY"):
        model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        roles["default"] = RoleConfig(parse_spec(f"google_genai:{model}"))
        roles["embeddings"] = RoleConfig(parse_spec(
            {"model": "google_genai:models/gemini-embedding-001", "dimensions": 768}
        ))
        sources.append("GEMINI_API_KEY")

    # 2. agentd.yaml
    path = Path(config_path or os.getenv("AGENTD_CONFIG") or DEFAULT_CONFIG_FILE)
    providers: Dict[str, Any] = {}
    if path.is_file():
        data = _load_yaml(path)
        providers = data.get("providers") or {}
        for role, value in (data.get("models") or {}).items():
            if role not in ("default", *ROLES):
                raise ModelConfigError(f"{path}: unknown model role {role!r}; use default or {', '.join(ROLES)}")
            roles[role] = _role_from_value(value)
        sources.append(str(path))

    # 3. Environment overrides
    for role in ("default", *ROLES):
        value = os.getenv(f"AGENTD_MODEL_{role.upper()}")
        if value:
            fallbacks = os.getenv(f"AGENTD_MODEL_{role.upper()}_FALLBACKS", "")
            roles[role] = _role_from_value({"model": value, "fallbacks": fallbacks})
            sources.append(f"AGENTD_MODEL_{role.upper()}")

    for role_cfg in roles.values():
        role_cfg.primary = _apply_provider_defaults(role_cfg.primary, providers)
        role_cfg.fallbacks = [_apply_provider_defaults(s, providers) for s in role_cfg.fallbacks]

    return ModelsConfig(roles=roles, source=", ".join(sources) or "none")
