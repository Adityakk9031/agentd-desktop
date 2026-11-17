"""`agentd doctor` — explain, in plain words, whether each model will work."""

import importlib.util
from dataclasses import dataclass
from typing import List, Optional

from agentd.models.client import message_text
from agentd.models.config import ROLES, ModelConfigError, ModelSpec, ModelsConfig, load_models_config
from agentd.models.factory import build_chat_model, build_embeddings

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    role: str
    model: str
    status: str
    message: str


def _module_for(package: str) -> str:
    return package.replace("-", "_")


def check_spec(role: str, spec: ModelSpec, ping: bool = False) -> Check:
    info = spec.info
    if info and importlib.util.find_spec(_module_for(info.package)) is None:
        return Check(role, spec.id, FAIL, f"package missing: pip install {info.package}")
    if spec.needs_key() and not spec.api_key():
        return Check(role, spec.id, FAIL, f"no API key: set {' or '.join(spec.key_envs())}")
    if not ping:
        note = "ready" if info else "unknown provider to doctor; may still work via LangChain"
        return Check(role, spec.id, OK if info else WARN, note)
    try:
        if role == "embeddings":
            vector = build_embeddings(spec).embed_query("ping")
            return Check(role, spec.id, OK, f"answered ({len(vector)} dimensions)")
        reply = build_chat_model(spec).invoke("Reply with the single word: pong")
        return Check(role, spec.id, OK, f"answered: {message_text(reply).strip()[:40]!r}")
    except ModelConfigError as exc:
        return Check(role, spec.id, FAIL, str(exc))
    except Exception as exc:
        return Check(role, spec.id, FAIL, f"{type(exc).__name__}: {str(exc)[:160]}")


def run_checks(config: Optional[ModelsConfig] = None, ping: bool = False) -> List[Check]:
    config = config or load_models_config()
    checks: List[Check] = []
    for role in ROLES:
        chain = config.chain(role)
        if not chain:
            status = WARN if role in ("verifier", "embeddings") else FAIL
            hint = {
                "verifier": "AI checking off; local checks only",
                "embeddings": "memory disabled",
            }.get(role, "nothing configured; set AGENTD_MODEL_DEFAULT or run `agentd init`")
            checks.append(Check(role, "-", status, hint))
            continue
        seen = set()
        for spec in chain:
            if (role, spec.id) in seen:
                continue
            seen.add((role, spec.id))
            checks.append(check_spec(role, spec, ping=ping))
    return checks


def format_report(checks: List[Check], source: str) -> str:
    icons = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    lines = [f"Model configuration from: {source}", ""]
    width = max((len(c.model) for c in checks), default=10)
    current = None
    for c in checks:
        role = c.role if c.role != current else ""
        current = c.role
        lines.append(f"  {role:<11} {icons[c.status]}  {c.model:<{width}}  {c.message}")
    usable = {c.role for c in checks if c.status == OK}
    missing = [r for r in ("planner", "worker") if r not in usable]
    lines.append("")
    lines.append(
        "Ready: planning and answering will work."
        if not missing
        else f"Not ready: no working model for {', '.join(missing)}."
    )
    return "\n".join(lines)
