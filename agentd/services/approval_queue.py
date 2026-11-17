"""Human approval queue — where Earned Autonomy asks for permission.

When the policy routes a task to ``review`` (low confidence, guardrail
blocks, or exhausted retries), the result is parked here instead of being
silently committed. Every channel (Telegram, Discord, Slack, web) renders
pending approvals as an Approve/Reject card; one tap resolves it.

Resolving with ``approved`` triggers the ``on_approve`` callback — wired to
the vector memory in main.py — so a human-confirmed result *earns its way*
into Pinecone and the trust record, exactly like an auto-committed one.
"""

import json
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("agentd.services.approval_queue")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Approval:
    """A parked result awaiting a human decision."""

    id: str
    channel: str
    chat_ref: str
    task: str
    plan: Dict[str, Any]
    executor_results: List[Dict[str, Any]]
    verification: Dict[str, Any]
    final_answer: Optional[str]
    confidence: float
    status: str  # "pending" | "approved" | "rejected"
    created_at: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "chat_ref": self.chat_ref,
            "task": self.task,
            "plan": self.plan,
            "executor_results": self.executor_results,
            "verification": self.verification,
            "final_answer": self.final_answer,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


class ApprovalQueue:
    """SQLite-backed store of pending approvals."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        on_approve: Optional[Callable[[Approval], None]] = None,
    ):
        self.db_path = db_path or os.path.join("data", "approvals.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.on_approve = on_approve
        self._init_db()

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:  # `with conn` commits, closing() closes
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- public

    def create(
        self,
        channel: str,
        chat_ref: str,
        task: str,
        plan: Dict[str, Any],
        executor_results: List[Dict[str, Any]],
        verification: Dict[str, Any],
        final_answer: Optional[str] = None,
    ) -> Approval:
        """Park a below-threshold result and return the approval record."""
        approval = Approval(
            id=uuid.uuid4().hex[:12],
            channel=channel,
            chat_ref=chat_ref,
            task=task,
            plan=plan,
            executor_results=executor_results,
            verification=verification,
            final_answer=final_answer,
            confidence=float((verification or {}).get("confidence", 0.0)),
            status="pending",
            created_at=_utc_now_iso(),
        )
        with closing(self._connect()) as conn, conn:  # `with conn` commits, closing() closes
            conn.execute(
                "INSERT INTO approvals (id, status, created_at, data) VALUES (?, ?, ?, ?)",
                (approval.id, approval.status, approval.created_at, json.dumps(approval.as_dict(), default=str)),
            )
        logger.info(f"Approval created: {approval.id} (task='{task[:60]}', confidence={approval.confidence:.2f})")
        return approval

    def get(self, approval_id: str) -> Optional[Approval]:
        with closing(self._connect()) as conn, conn:  # `with conn` commits, closing() closes
            row = conn.execute(
                "SELECT data FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return self._row_to_approval(row)

    def list(self, status: str = "pending", limit: int = 50) -> List[Approval]:
        with closing(self._connect()) as conn, conn:  # `with conn` commits, closing() closes
            rows = conn.execute(
                "SELECT data FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [a for a in (self._row_to_approval(row) for row in rows) if a]

    def resolve(
        self, approval_id: str, decision: str, resolved_by: Optional[str] = None
    ) -> Optional[Approval]:
        """
        Resolve a pending approval with "approved" or "rejected".

        Returns the updated Approval, or None when the id is unknown or
        already resolved. Approving fires the ``on_approve`` callback.
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"Invalid approval decision: {decision!r}")

        approval = self.get(approval_id)
        if approval is None or approval.status != "pending":
            return None

        approval.status = decision
        approval.resolved_at = _utc_now_iso()
        approval.resolved_by = resolved_by

        with closing(self._connect()) as conn, conn:  # `with conn` commits, closing() closes
            conn.execute(
                "UPDATE approvals SET status = ?, data = ? WHERE id = ?",
                (decision, json.dumps(approval.as_dict(), default=str), approval_id),
            )

        logger.info(f"Approval {approval_id} -> {decision} (by {resolved_by or 'unknown'})")

        if decision == "approved" and self.on_approve is not None:
            try:
                self.on_approve(approval)
            except Exception as exc:
                logger.error(f"on_approve callback failed: {exc}")

        return approval

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _row_to_approval(row: Optional[sqlite3.Row]) -> Optional[Approval]:
        if row is None:
            return None
        try:
            data = json.loads(row["data"])
            return Approval(
                id=data["id"],
                channel=data["channel"],
                chat_ref=data["chat_ref"],
                task=data["task"],
                plan=data.get("plan") or {},
                executor_results=data.get("executor_results") or [],
                verification=data.get("verification") or {},
                final_answer=data.get("final_answer"),
                confidence=float(data.get("confidence", 0.0)),
                status=data["status"],
                created_at=data["created_at"],
                resolved_at=data.get("resolved_at"),
                resolved_by=data.get("resolved_by"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error(f"Corrupt approval record: {exc}")
            return None
