"""Automations — tasks the agent runs on a schedule.

An automation is a task in plain words plus a schedule:

- ``interval``: every N minutes
- ``daily``:    every day at HH:MM (this computer's local time)

A background loop runs whatever is due. Results are stored with the
automation; a result that needs review also goes to the approval queue.
Stored in SQLite in the local data folder.
"""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("agentd.services.automations")

KINDS = ("interval", "daily")
MODES = ("single", "team")
MIN_INTERVAL_MINUTES = 5

Runner = Callable[[str, str], Awaitable[Dict[str, Any]]]  # (task, mode) -> workflow result


class AutomationError(ValueError):
    """Invalid automation settings."""


@dataclass
class Automation:
    id: str
    name: str
    task: str
    mode: str = "single"
    kind: str = "daily"
    every_minutes: int = 60
    daily_time: str = "09:00"
    model: str = ""          # "provider:model", or "" to use whatever the chat uses
    weekdays: List[int] = field(default_factory=list)  # daily only: 0=Mon..6=Sun; empty = every day
    effort: str = ""         # "low" | "medium" | "high"; "" = the model's default
    enabled: bool = True
    created_at: str = ""
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None   # "ok" | "review" | "failed"
    last_result: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schedule"] = describe_schedule(self)
        return data


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_LATE = timedelta(hours=12)  # a run missed while the computer slept still starts, up to this late


def describe_schedule(a: Automation) -> str:
    if a.kind == "daily":
        days = sorted(set(a.weekdays or []))
        if not days or len(days) == 7:
            return f"Every day at {a.daily_time}"
        if days == [0, 1, 2, 3, 4]:
            return f"Every weekday at {a.daily_time}"
        if days == [5, 6]:
            return f"Every weekend at {a.daily_time}"
        return f"{', '.join(DAY_NAMES[d] for d in days)} at {a.daily_time}"
    if a.every_minutes % 60 == 0:
        hours = a.every_minutes // 60
        return "Every hour" if hours == 1 else f"Every {hours} hours"
    return f"Every {a.every_minutes} minutes"


def next_run(a: Automation, now: datetime) -> datetime:
    """When this automation should next run, strictly after ``now``."""
    if a.kind == "interval":
        return now + timedelta(minutes=a.every_minutes)
    hour, minute = (int(part) for part in a.daily_time.split(":"))
    allowed = set(a.weekdays or []) or set(range(7))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() not in allowed:
        candidate += timedelta(days=1)
    return candidate


def validate(name: str, task: str, mode: str, kind: str, every_minutes: int, daily_time: str, model: str = "",
             weekdays: Optional[List[int]] = None) -> None:
    if weekdays and not all(isinstance(d, int) and 0 <= d <= 6 for d in weekdays):
        raise AutomationError("Weekdays must be numbers from 0 (Monday) to 6 (Sunday).")
    if model and (":" not in model or not all(part.strip() for part in model.split(":", 1))):
        raise AutomationError("Model must look like provider:model.")
    if not (name or "").strip():
        raise AutomationError("Give the automation a name.")
    if not (task or "").strip():
        raise AutomationError("Describe the task to run.")
    if mode not in MODES:
        raise AutomationError(f"Mode must be one of: {', '.join(MODES)}")
    if kind not in KINDS:
        raise AutomationError(f"Schedule must be one of: {', '.join(KINDS)}")
    if kind == "interval" and int(every_minutes) < MIN_INTERVAL_MINUTES:
        raise AutomationError(f"The shortest interval is {MIN_INTERVAL_MINUTES} minutes.")
    if kind == "daily":
        try:
            hour, minute = (int(part) for part in str(daily_time).split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except ValueError:
            raise AutomationError("Daily time must look like 09:30.")


class AutomationStore:
    """SQLite-backed list of automations."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join("data", "automations.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS automations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _save(self, a: Automation) -> Automation:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO automations (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (a.id, json.dumps(asdict(a))),
            )
        return a

    def list(self) -> List[Automation]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT data FROM automations").fetchall()
        known = set(Automation.__dataclass_fields__)
        items = [Automation(**{k: v for k, v in json.loads(row["data"]).items() if k in known}) for row in rows]
        return sorted(items, key=lambda a: a.created_at, reverse=True)

    def get(self, automation_id: str) -> Optional[Automation]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT data FROM automations WHERE id = ?", (automation_id,)).fetchone()
        if not row:
            return None
        known = set(Automation.__dataclass_fields__)
        return Automation(**{k: v for k, v in json.loads(row["data"]).items() if k in known})

    def create(self, name: str, task: str, mode: str = "single", kind: str = "daily",
               every_minutes: int = 60, daily_time: str = "09:00", model: str = "",
               weekdays: Optional[List[int]] = None, effort: str = "", now: Optional[datetime] = None) -> Automation:
        model = (model or "").strip()
        weekdays = sorted(set(weekdays or []))
        effort = (effort or "").strip().lower()
        if effort not in ("", "low", "medium", "high"):
            raise AutomationError("Effort must be low, medium or high.")
        validate(name, task, mode, kind, every_minutes, daily_time, model, weekdays)
        now = now or datetime.now()
        a = Automation(
            id=uuid.uuid4().hex[:12], name=name.strip()[:80], task=task.strip(), mode=mode, kind=kind,
            every_minutes=int(every_minutes), daily_time=daily_time, model=model, weekdays=weekdays, effort=effort,
            created_at=now.isoformat(timespec="seconds"),
        )
        a.next_run_at = next_run(a, now).isoformat(timespec="seconds")
        return self._save(a)

    def set_enabled(self, automation_id: str, enabled: bool, now: Optional[datetime] = None) -> Optional[Automation]:
        a = self.get(automation_id)
        if a is None:
            return None
        a.enabled = bool(enabled)
        a.next_run_at = next_run(a, now or datetime.now()).isoformat(timespec="seconds") if a.enabled else None
        return self._save(a)

    def delete(self, automation_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,)).rowcount > 0

    def record_run(self, automation_id: str, status: str, result: str, now: Optional[datetime] = None) -> Optional[Automation]:
        a = self.get(automation_id)
        if a is None:
            return None
        now = now or datetime.now()
        a.last_run_at = now.isoformat(timespec="seconds")
        a.last_status = status
        a.last_result = (result or "")[:4000]
        if a.enabled:
            a.next_run_at = next_run(a, now).isoformat(timespec="seconds")
        return self._save(a)

    def due(self, now: Optional[datetime] = None) -> List[Automation]:
        """Automations to run now. One missed by more than MAX_LATE is moved to its next time instead."""
        now = now or datetime.now()
        stamp = now.isoformat(timespec="seconds")
        ready = []
        for a in self.list():
            if not (a.enabled and a.next_run_at and a.next_run_at <= stamp):
                continue
            if now - datetime.fromisoformat(a.next_run_at) > MAX_LATE:
                logger.info(f"Automation '{a.name}' missed its time by more than 12 hours; skipping to the next one")
                a.next_run_at = next_run(a, now).isoformat(timespec="seconds")
                a.last_status, a.last_result = "skipped", "Missed while this computer was off or asleep."
                self._save(a)
                continue
            ready.append(a)
        return ready


class AutomationScheduler:
    """Runs due automations in the background, one run per automation at a time."""

    def __init__(self, store: AutomationStore, runner: Runner, approval_queue: Any = None, poll_seconds: float = 20.0):
        self.store = store
        self.runner = runner
        self.approval_queue = approval_queue
        self.poll_seconds = poll_seconds
        self.running: set = set()
        self._loop_task: Optional[asyncio.Task] = None
        self._tasks: set = set()

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("Automation scheduler started")

    async def stop(self) -> None:
        for task in [self._loop_task, *self._tasks]:
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in [self._loop_task, *self._tasks] if t], return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                for automation in self.store.due():
                    self.launch(automation.id)
            except Exception as exc:  # never let the loop die
                logger.error(f"Automation scheduler error: {exc}")
            await asyncio.sleep(self.poll_seconds)

    def launch(self, automation_id: str) -> bool:
        """Start a run in the background. False if it is already running."""
        if automation_id in self.running:
            return False
        self.running.add(automation_id)
        task = asyncio.create_task(self.run_now(automation_id, _claimed=True))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def run_now(self, automation_id: str, _claimed: bool = False) -> Optional[Automation]:
        automation = self.store.get(automation_id)
        if automation is None:
            self.running.discard(automation_id)
            return None
        if not _claimed:
            if automation_id in self.running:
                return automation
            self.running.add(automation_id)
        logger.info(f"Automation '{automation.name}' starting (model: {automation.model or 'default'})")
        from agentd.models.config import reset_model_override, set_model_override

        override = set_model_override(automation.model)  # only for this run
        from agentd.models.config import reset_effort, set_effort

        effort_token = set_effort(automation.effort) if automation.effort else None
        try:
            result = await self.runner(automation.task, automation.mode)
            answer = result.get("final_answer") or ((result.get("verification") or {}).get("final_output") or {}).get("summary") or "Done."
            status = "review" if result.get("needs_review") else "ok"
            if status == "review" and self.approval_queue is not None:
                self.approval_queue.create(
                    channel="automation", chat_ref=automation.id, task=f"[{automation.name}] {automation.task}",
                    plan=result.get("plan") or {}, executor_results=result.get("executor_results") or [],
                    verification=result.get("verification") or {}, final_answer=result.get("final_answer"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Automation '{automation.name}' failed: {exc}")
            status, answer = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
        finally:
            reset_model_override(override)
            if effort_token is not None:
                reset_effort(effort_token)
            self.running.discard(automation_id)
        return self.store.record_run(automation_id, status, answer)
