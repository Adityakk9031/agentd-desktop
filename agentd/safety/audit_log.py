"""Audit log — a durable record of what the agent decided and did.

Every permission check, verification and human approval is written here with
its outcome, so a user can always answer "what did the agent do, and why?".
Backed by SQLite (stdlib only) in the local data folder.
"""

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DEFAULT_DB_PATH = os.path.join("data", "audit.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    node TEXT NOT NULL,
    event TEXT NOT NULL,
    value TEXT,
    confidence REAL,
    outcome TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_node ON audit_events(node);
"""


class AuditLog:
    """Append-only log of agent events, backed by SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("AUDIT_LOG_DB", DEFAULT_DB_PATH)
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def log(
        self,
        node: str,
        event: str,
        value: Any = None,
        confidence: Optional[float] = None,
        outcome: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record one event. Returns its id."""
        event_id = str(uuid.uuid4())
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO audit_events (id, created_at, node, event, value,"
                " confidence, outcome, payload_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    node,
                    event[:500],
                    None if value is None else str(value)[:500],
                    confidence,
                    outcome,
                    json.dumps(payload, default=str) if payload is not None else None,
                ),
            )
        return event_id

    def set_outcome(self, event_id: str, outcome: str) -> None:
        """Update the outcome of a previously logged event."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE audit_events SET outcome = ? WHERE id = ?", (outcome, event_id)
            )

    def stats(self) -> Dict[str, Any]:
        """Aggregate counts for GET /api/audit/stats."""
        with closing(self._connect()) as conn:
            total = conn.execute("SELECT COUNT(*) AS total FROM audit_events").fetchone()
            by_node = conn.execute(
                "SELECT node, COUNT(*) AS count FROM audit_events GROUP BY node ORDER BY count DESC"
            ).fetchall()
            by_outcome = conn.execute(
                "SELECT COALESCE(outcome, 'none') AS outcome, COUNT(*) AS count"
                " FROM audit_events GROUP BY outcome ORDER BY count DESC"
            ).fetchall()
        return {
            "total_events": int(total["total"] or 0),
            "by_node": {r["node"]: int(r["count"]) for r in by_node},
            "by_outcome": {r["outcome"]: int(r["count"]) for r in by_outcome},
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
