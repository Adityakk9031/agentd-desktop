"""LLMClient — one role's models, tried in order.

If the first model fails (key out of credit, rate limit, provider outage,
missing package or key), the same request goes to the next model in the
role's chain. The caller never has to switch anything by hand.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from agentd.models.config import ModelConfigError, ModelSpec, ModelsConfig, config_version, load_models_config
from agentd.models.config import current_effort, reset_effort, set_effort
from agentd.models.factory import build_chat_model, is_effort_error

logger = logging.getLogger("agentd.models.client")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class NoModelConfigured(ModelConfigError):
    """No model is configured for this role."""


class AllModelsFailed(RuntimeError):
    """Every model in the chain failed; ``errors`` lists why, per model."""

    def __init__(self, role: str, errors: List[str]):
        self.role = role
        self.errors = errors
        super().__init__(f"All models for role '{role}' failed: " + " | ".join(errors))


def message_text(message: Any) -> str:
    """Plain text of a chat response (string content or content blocks)."""
    text = getattr(message, "text", None)
    if isinstance(text, str):  # current langchain-core: a str property
        return str(text)
    if callable(text):  # older langchain-core exposed .text() as a method
        text = text()
        if isinstance(text, str):
            return text
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def parse_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from model output, tolerating code fences and chatter."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"Model did not return JSON: {text[:200]!r}")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Model returned JSON that is not an object")
    return value


class LLMClient:
    """Calls the models configured for one role, falling back in order."""

    def __init__(
        self,
        role: str,
        config: Optional[ModelsConfig] = None,
        model_factory: Callable[[ModelSpec], Any] = build_chat_model,
    ):
        self.role = role
        self._config = config
        self._injected = config is not None   # tests / explicit config never reload
        self._loaded_version = -1
        self._factory = model_factory
        self._models: Dict[str, Any] = {}       # built lazily, cached per spec id
        self.last_model: Optional[str] = None   # which model answered last
        self.logger = logging.getLogger(f"agentd.models.{role}")

    @property
    def config(self) -> ModelsConfig:
        if self._injected:
            return self._config
        version = config_version()
        if self._config is None or version != self._loaded_version:
            # Settings changed (or first use): reload and drop cached models
            self._config = load_models_config()
            self._models = {}
            self._loaded_version = version
        return self._config

    @property
    def chain(self) -> List[ModelSpec]:
        return self.config.chain(self.role)

    @property
    def configured(self) -> bool:
        return bool(self.chain)

    def _model(self, spec: ModelSpec):
        key = f"{spec.id}|{current_effort()}"  # a different effort is a differently configured model
        if key not in self._models:
            self._models[key] = self._factory(spec)
        return self._models[key]

    def _ask(self, spec: ModelSpec, messages):
        try:
            return self._model(spec).invoke(messages)
        except Exception as exc:
            if not (current_effort() and is_effort_error(exc)):
                raise
            # This model does not take an effort setting: ask again without it
            self.logger.info(f"{spec.id} does not accept an effort level; using its default")
            token = set_effort(None)
            try:
                return self._model(spec).invoke(messages)
            finally:
                reset_effort(token)

    def invoke_text(self, system_instruction: str, user_prompt: str) -> str:
        """Send one request; try each model in the chain until one answers."""
        chain = self.chain
        if not chain:
            raise NoModelConfigured(
                f"No model configured for '{self.role}'. Set AGENTD_MODEL_DEFAULT "
                "(e.g. openai:gpt-5.4-mini) or run `agentd init`."
            )
        errors: List[str] = []
        for spec in chain:
            try:
                response = self._ask(spec, [("system", system_instruction), ("human", user_prompt)])
                self.last_model = spec.id
                if errors:
                    self.logger.warning(f"Answered by fallback {spec.id} after: {'; '.join(errors)}")
                return message_text(response)
            except Exception as exc:  # any failure -> next model
                reason = f"{spec.id}: {type(exc).__name__}: {str(exc)[:200]}"
                self.logger.warning(f"Model failed, trying next: {reason}")
                errors.append(reason)
        raise AllModelsFailed(self.role, errors)

    def generate_json(self, system_instruction: str, user_prompt: str) -> Dict[str, Any]:
        return parse_json(self.invoke_text(system_instruction, user_prompt))

    def validate_and_retry_json(
        self,
        system_instruction: str,
        user_prompt: str,
        validator: Callable[[Dict[str, Any]], bool],
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Generate JSON, re-asking when it is unparseable or fails ``validator``."""
        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                result = self.generate_json(system_instruction, user_prompt)
                if validator(result):
                    return result
                last_error = ValueError("Response did not match the required schema")
            except (AllModelsFailed, NoModelConfigured):
                raise  # switching prompts will not fix a dead model chain
            except ValueError as exc:
                last_error = exc
            if attempt < max_retries:
                self.logger.warning(f"Invalid response, retrying ({attempt + 1}/{max_retries}): {last_error}")
                user_prompt += (
                    "\n\nPREVIOUS RESPONSE WAS INVALID. Respond ONLY with JSON matching the required schema."
                )
        raise ValueError(f"Failed to get valid JSON after {max_retries + 1} attempts: {last_error}")
