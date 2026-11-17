"""Tool permissions — rules enforced by code, not by the prompt.

Every planned tool call is checked here BEFORE it runs. The model cannot
forget or talk its way past these rules, because they never live in its
context: the runtime applies them to each step.

Each rule maps a tool-name glob (``fnmatch`` syntax, case-insensitive) to an
action:

- ``allow``: run without asking.
- ``ask``:   needs a human decision; the step is held and the run goes to review.
- ``deny``:  never run.

Rule precedence: deny > ask > allow > default. The first matching pattern in
the strictest list wins, so a broad allow can never override a specific deny.

Configured from the environment (comma-separated globs):

    PERMISSION_DEFAULT=allow        # allow | ask | deny
    PERMISSION_ALLOW=github_*,weather_*
    PERMISSION_ASK=*_post*,*_send*
    PERMISSION_DENY=*delete*,*drop*
"""

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger("agentd.safety.permissions")

ALLOW, ASK, DENY = "allow", "ask", "deny"
_ACTIONS = (ALLOW, ASK, DENY)

# Destructive or outward-facing verbs that always need a human by default.
DEFAULT_ASK = ["*delete*", "*remove*", "*drop*", "*post*", "*send*", "*publish*", "*pay*", "*purchase*", "*order*"]


def _split(raw: str) -> List[str]:
    return [p.strip().lower() for p in (raw or "").split(",") if p.strip()]


@dataclass
class PermissionPolicy:
    """Decides allow / ask / deny for a tool name."""

    default: str = ALLOW
    allow: List[str] = field(default_factory=list)
    ask: List[str] = field(default_factory=lambda: list(DEFAULT_ASK))
    deny: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.default not in _ACTIONS:
            raise ValueError(f"Invalid default permission {self.default!r}; use one of {_ACTIONS}")

    @classmethod
    def from_env(cls) -> "PermissionPolicy":
        ask_env = os.getenv("PERMISSION_ASK")
        return cls(
            default=os.getenv("PERMISSION_DEFAULT", ALLOW).strip().lower(),
            allow=_split(os.getenv("PERMISSION_ALLOW", "")),
            ask=_split(ask_env) if ask_env is not None else list(DEFAULT_ASK),
            deny=_split(os.getenv("PERMISSION_DENY", "")),
        )

    def check(self, tool_name: str) -> Tuple[str, str]:
        """Return (action, reason) for a tool name."""
        name = str(tool_name or "").lower()
        for action, patterns in ((DENY, self.deny), (ASK, self.ask), (ALLOW, self.allow)):
            for pattern in patterns:
                if fnmatch.fnmatchcase(name, pattern):
                    return action, f"rule '{pattern}' -> {action}"
        return self.default, f"default -> {self.default}"
