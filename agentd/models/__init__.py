"""Bring your own model: per-role configuration, provider factory, fallback client."""

from agentd.models.client import AllModelsFailed, LLMClient, NoModelConfigured
from agentd.models.config import (
    CHAT_ROLES,
    PROVIDERS,
    ROLES,
    ModelConfigError,
    ModelsConfig,
    ModelSpec,
    load_models_config,
    parse_spec,
)
from agentd.models.factory import MissingProviderPackage, build_chat_model, build_embeddings

__all__ = [
    "AllModelsFailed", "LLMClient", "NoModelConfigured", "CHAT_ROLES", "PROVIDERS", "ROLES",
    "ModelConfigError", "ModelsConfig", "ModelSpec", "load_models_config", "parse_spec",
    "MissingProviderPackage", "build_chat_model", "build_embeddings",
]
