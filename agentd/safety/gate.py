"""Action gate — the agent pauses and asks before it changes anything.

Every tool that writes a file, runs a command or opens something calls
``await gate.request(...)`` first. In "ask" mode the run waits there until a
person answers (allow once / allow for the rest of this task / deny). In
"auto" mode it proceeds without asking. Either way the hard rules in
agentd.computer.safety are applied before the gate is even reached.

The gate lives outside the model: nothing the model writes can answer it.
"""

import asyncio
import uuid
from typing import Any, Callable, Dict, Optional

ASK, AUTO = "ask", "auto"
ONCE, ALWAYS, DENY = "once", "always", "deny"


class ActionGate:
    def __init__(
        self,
        mode: str = ASK,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        interactive: bool = True,
        audit_log: Any = None,
        timeout_seconds: float = 900,
    ):
        self.mode = mode if mode in (ASK, AUTO) else ASK
        self.on_event = on_event
        self.interactive = interactive      # False: nobody is there to answer (automations)
        self.audit = audit_log
        self.timeout = timeout_seconds
        self.pending: Dict[str, asyncio.Future] = {}
        self.allowed_kinds: set = set()     # kinds the user allowed for the rest of this task

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _record(self, kind: str, title: str, outcome: str) -> None:
        if self.audit is not None:
            self.audit.log(node="gate", event=f"{kind}: {title}"[:500], value=outcome, outcome=outcome)

    async def request(self, kind: str, title: str, detail: str = "", force: bool = False) -> bool:
        """True when the action may run. ``force`` asks even with full access (payments, handing over to the user)."""
        if not force and (self.mode == AUTO or kind in self.allowed_kinds):
            self._record(kind, title, "allowed")
            return True
        if not self.interactive:
            self._record(kind, title, "declined-unattended")
            return False

        action_id = uuid.uuid4().hex[:12]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[action_id] = future
        self._emit({"type": "approval", "id": action_id, "kind": kind, "title": title, "detail": detail[:4000],
                    "once_only": force})
        try:
            decision = await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            decision = DENY
        finally:
            self.pending.pop(action_id, None)
        if decision == ALWAYS and not force:
            self.allowed_kinds.add(kind)
        self._emit({"type": "approval_resolved", "id": action_id, "decision": decision})
        self._record(kind, title, "denied" if decision == DENY else "approved")
        return decision in (ONCE, ALWAYS)

    def resolve(self, action_id: str, decision: str) -> bool:
        """Answer a pending request. False if there is no such request."""
        future = self.pending.get(action_id)
        if future is None or future.done() or decision not in (ONCE, ALWAYS, DENY):
            return False
        future.get_loop().call_soon_threadsafe(lambda: future.done() or future.set_result(decision))
        return True

    def cancel_all(self) -> None:
        for action_id in list(self.pending):
            self.resolve(action_id, DENY)
