"""Channel layer — one agent brain, many doors.

Telegram, Discord, Slack (and the web UI later) all route through
:class:`ChannelManager`: an inbound message becomes a task, the workflow
runs, and the reply carries a verification badge.
Below-threshold results become approval cards with Approve/Reject buttons
instead of being silently committed.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentd.channels.base")

_BADGE_LABELS = {
    "llm": "AI-checked",
    "local": "Checked",
}


def format_score_badge(verification: Dict[str, Any]) -> str:
    """Render the badge shown under every reply, e.g. `✅ AI-checked 94%`."""
    if not verification:
        return ""
    source = str(verification.get("source", "local"))
    try:
        pct = int(round(float(verification.get("confidence", 0.0)) * 100))
    except (TypeError, ValueError):
        pct = 0
    label = _BADGE_LABELS.get(source, "Checked")
    return f"✅ {label} {pct}%"


def format_task_reply(task: str, result: Dict[str, Any]) -> str:
    """Human-facing reply for a completed (committed) task."""
    verification = result.get("verification") or {}
    lines: list[str] = []

    final_answer = result.get("final_answer")
    if final_answer:
        lines.append(str(final_answer)[:3500])
    else:
        summary = (verification.get("final_output") or {}).get("summary")
        if summary:
            lines.append(str(summary)[:3500])
        else:
            evidence = verification.get("final_output", {}).get("evidence") or []
            for ev in evidence[:5]:
                data = ev.get("data") or {}
                keys = ", ".join(str(k) for k in list(data)[:5]) if isinstance(data, dict) else ""
                lines.append(f"• {ev.get('tool', 'step')}: {keys or 'done'}")

    if not lines:
        lines.append(f"Task completed: {task}")

    lines.append("")
    lines.append(format_score_badge(verification))
    return "\n".join(lines)


@dataclass
class ActionCard:
    """A question from a running agent, shown with Approve / Reject buttons."""

    id: str
    chat_ref: str
    task: str                      # what the agent wants to do
    final_answer: Optional[str]    # the detail (command, file contents, ...)
    kind: str = "action"
    confidence: float = 1.0
    verification: Dict[str, Any] = field(default_factory=dict)


def format_approval_card(approval) -> str:
    """The text shown above Approve/Reject buttons on every channel."""
    if getattr(approval, "kind", "") == "action":
        lines = [f"\U0001F512 The agent wants to: {str(approval.task)[:300]}"]
        if approval.final_answer:
            lines += ["", str(approval.final_answer)[:1500]]
        lines += ["", "Approve to let it continue, reject to skip this step."]
        return "\n".join(lines)
    try:
        conf = int(round(float(approval.confidence or 0.0) * 100))
    except (TypeError, ValueError):
        conf = 0

    lines = [f"🔒 Approval needed (confidence {conf}%)", f"Task: {str(approval.task)[:300]}"]

    if approval.final_answer:
        lines.append("")
        lines.append(str(approval.final_answer)[:1500])
    else:
        summary = ((approval.verification or {}).get("final_output") or {}).get("summary")
        if summary:
            lines.append("")
            lines.append(str(summary)[:1500])

    lines.append("")
    lines.append("This fell below your autonomy threshold — approve to commit and save it to memory, reject to log a correction.")
    return "\n".join(lines)


class MessagingChannel(ABC):
    """Base class for every notification/chat integration."""

    name: str = "channel"

    def __init__(self):
        self.manager: Optional["ChannelManager"] = None
        self.logger = logging.getLogger(f"channels.{self.name}")

    def attach(self, manager: "ChannelManager") -> None:
        self.manager = manager

    @abstractmethod
    async def start(self) -> None:
        """Begin receiving events (no-op when unconfigured)."""

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect cleanly."""

    @abstractmethod
    async def send_text(self, chat_ref: str, text: str) -> None:
        """Send a plain message to a chat/channel reference."""

    @abstractmethod
    async def send_approval(self, approval) -> None:
        """Render an Approval as an interactive Approve/Reject card."""


class ChannelManager:
    """Routes inbound messages into the workflow and approvals back out."""

    def __init__(self, workflow_service, approval_queue):
        self.workflow_service = workflow_service
        self.approval_queue = approval_queue
        self.channels: Dict[str, MessagingChannel] = {}
        self.pending_actions: Dict[str, Any] = {}          # question id -> the run's gate
        self.histories: Dict[str, List[Dict[str, str]]] = {}  # per chat, so follow-ups make sense
        self.logger = logging.getLogger("agentd.channels.manager")

    def register(self, channel: MessagingChannel) -> None:
        channel.attach(self)
        self.channels[channel.name] = channel
        self.logger.info(f"Registered channel: {channel.name}")

    async def start_all(self) -> None:
        for channel in self.channels.values():
            await channel.start()

    async def stop_all(self) -> None:
        for channel in self.channels.values():
            try:
                await channel.stop()
            except Exception as exc:
                self.logger.warning(f"Error stopping {channel.name}: {exc}")

    # ------------------------------------------------------------- inbound

    async def handle_text(
        self, channel: MessagingChannel, text: str, chat_ref: str
    ) -> Optional[str]:
        """
        Run an inbound message through the workflow.

        Returns the reply text to send, or None when an approval card was
        already sent instead (the caller should not double-reply).
        """
        text = (text or "").strip()
        if not text:
            return None

        self.logger.info(f"[{channel.name}] Task from chat {chat_ref}: {text[:80]}")
        return await self._run_agent(channel, text, chat_ref)

    async def _park_for_review(self, channel: "MessagingChannel", text: str, chat_ref: str, result: Dict[str, Any]) -> Optional[str]:
        """A result that needs a person goes to the approval queue and arrives as a card."""
        if result.get("needs_review"):
            approval = self.approval_queue.create(
                channel=channel.name,
                chat_ref=chat_ref,
                task=text,
                plan=result.get("plan") or {},
                executor_results=result.get("executor_results") or [],
                verification=result.get("verification") or {},
                final_answer=result.get("final_answer"),
            )
            await channel.send_approval(approval)
            return None

        return format_task_reply(text, result)

    async def _run_agent(self, channel: MessagingChannel, text: str, chat_ref: str) -> str:
        """Run the tool-using agent; its questions arrive in the chat as buttons."""
        from agentd.safety.gate import ActionGate

        loop = asyncio.get_running_loop()
        key = f"{channel.name}:{chat_ref}"
        history = self.histories.setdefault(key, [])
        asked: List[str] = []

        def on_event(event: Dict[str, Any]) -> None:
            if event.get("type") != "approval":
                return
            self.pending_actions[event["id"]] = gate
            asked.append(event["id"])
            card = ActionCard(id=event["id"], chat_ref=chat_ref, task=event["title"], final_answer=event.get("detail"))
            loop.create_task(channel.send_approval(card))

        gate = ActionGate(mode="ask", on_event=on_event, interactive=True)
        try:
            result = await self.workflow_service.run_agent(text, on_event=on_event, gate=gate, history=list(history))
        finally:
            for action_id in asked:
                self.pending_actions.pop(action_id, None)
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": result.get("final_answer") or ""}]
        del history[:-12]
        return await self._park_for_review(channel, text, chat_ref, result)

    async def handle_approval_callback(
        self, approval_id: str, decision: str, resolved_by: Optional[str] = None
    ) -> str:
        """Resolve an approval from a button tap (or the web API)."""
        gate = self.pending_actions.pop(approval_id, None)
        if gate is not None:  # a question from an agent that is still running
            allowed = decision == "approved"
            if not gate.resolve(approval_id, "once" if allowed else "deny"):
                return "That question is no longer waiting for an answer."
            return "Allowed. The agent is continuing." if allowed else "Denied. The agent will skip that step."
        approval = self.approval_queue.resolve(approval_id, decision, resolved_by)
        if approval is None:
            return "That approval was already handled or does not exist."
        if decision == "approved":
            return "✅ Approved — result committed and the approach saved to memory."
        return "❌ Rejected — logged as a correction."
