"""Offline tests for scheduled automations."""

from datetime import datetime

import pytest

from agentd.services.automations import AutomationError, AutomationScheduler, AutomationStore, next_run


@pytest.fixture
def store(tmp_path):
    return AutomationStore(db_path=str(tmp_path / "automations.db"))


NOON = datetime(2026, 11, 16, 12, 0, 0)


def test_daily_schedule_runs_today_or_tomorrow(store):
    later = store.create("Evening", "Summarise", kind="daily", daily_time="18:30", now=NOON)
    earlier = store.create("Morning", "Summarise", kind="daily", daily_time="09:00", now=NOON)

    assert later.next_run_at == "2026-11-16T18:30:00"
    assert earlier.next_run_at == "2026-11-17T09:00:00"
    assert later.as_dict()["schedule"] == "Every day at 18:30"


def test_interval_schedule(store):
    a = store.create("Check", "Check the site", kind="interval", every_minutes=120, now=NOON)
    assert a.next_run_at == "2026-11-16T14:00:00"
    assert a.as_dict()["schedule"] == "Every 2 hours"
    assert next_run(a, NOON) > NOON


@pytest.mark.parametrize("kwargs", [
    {"name": "", "task": "x"},
    {"name": "x", "task": ""},
    {"name": "x", "task": "x", "mode": "swarm"},
    {"name": "x", "task": "x", "kind": "interval", "every_minutes": 1},
    {"name": "x", "task": "x", "kind": "daily", "daily_time": "25:00"},
])
def test_invalid_automations_are_rejected(store, kwargs):
    with pytest.raises(AutomationError):
        store.create(**kwargs)


def test_due_only_returns_enabled_and_past_due(store):
    a = store.create("A", "task", kind="interval", every_minutes=30, now=NOON)
    b = store.create("B", "task", kind="interval", every_minutes=30, now=NOON)
    store.set_enabled(b.id, False)

    assert store.due(now=datetime(2026, 11, 16, 12, 10)) == []
    assert [x.id for x in store.due(now=datetime(2026, 11, 16, 12, 31))] == [a.id]


@pytest.mark.asyncio
async def test_run_records_result_and_reschedules(store):
    a = store.create("A", "Say hi", mode="team", kind="interval", every_minutes=30, now=NOON)
    seen = []

    async def runner(task, mode, **_):
        seen.append((task, mode))
        return {"final_answer": "Hi!", "needs_review": False}

    done = await AutomationScheduler(store, runner).run_now(a.id)

    assert seen == [("Say hi", "team")]
    assert done.last_status == "ok" and done.last_result == "Hi!"
    assert done.next_run_at > done.last_run_at


@pytest.mark.asyncio
async def test_failed_run_is_recorded_not_raised(store):
    a = store.create("A", "task", kind="interval", every_minutes=30, now=NOON)

    async def runner(task, mode, **_):
        raise RuntimeError("no model configured")

    scheduler = AutomationScheduler(store, runner)
    done = await scheduler.run_now(a.id)

    assert done.last_status == "failed" and "no model configured" in done.last_result
    assert scheduler.running == set()


@pytest.mark.asyncio
async def test_review_results_go_to_the_approval_queue(store):
    a = store.create("A", "risky task", kind="interval", every_minutes=30, now=NOON)
    parked = []

    class Queue:
        def create(self, **kwargs):
            parked.append(kwargs)

    async def runner(task, mode, **_):
        return {"final_answer": "Draft", "needs_review": True, "plan": {}, "executor_results": [], "verification": {}}

    done = await AutomationScheduler(store, runner, approval_queue=Queue()).run_now(a.id)

    assert done.last_status == "review"
    assert parked[0]["channel"] == "automation" and "risky task" in parked[0]["task"]


def test_delete(store):
    a = store.create("A", "task", now=NOON)
    assert store.delete(a.id) is True and store.get(a.id) is None and store.delete(a.id) is False


@pytest.mark.asyncio
async def test_automation_runs_with_its_own_model(store, monkeypatch):
    from agentd.models.config import load_models_config

    for name in ("GEMINI_API_KEY", "AGENTD_CONFIG", "AGENTD_MODEL_DEFAULT_FALLBACKS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTD_MODEL_DEFAULT", "openai:gpt-5.4")
    a = store.create("A", "task", kind="interval", every_minutes=30, model="xai:grok-4.7", now=NOON)
    seen = {}

    async def runner(task, mode, **_):
        seen["during"] = [s.id for s in load_models_config().chain("planner")]
        return {"final_answer": "ok"}

    await AutomationScheduler(store, runner).run_now(a.id)

    assert seen["during"] == ["xai:grok-4.7", "openai:gpt-5.4"]  # its model first, default as fallback
    assert [s.id for s in load_models_config().chain("planner")] == ["openai:gpt-5.4"]  # default untouched
    assert store.get(a.id).model == "xai:grok-4.7"


def test_automation_model_must_be_provider_and_model(store):
    with pytest.raises(AutomationError):
        store.create("A", "task", model="gpt-5.4", now=NOON)



@pytest.mark.asyncio
async def test_automation_runs_with_its_own_effort(store):
    from agentd.models.config import current_effort

    a = store.create("A", "task", kind="interval", every_minutes=30, effort="low", now=NOON)
    seen = {}

    async def runner(task, mode, **_):
        seen["effort"] = current_effort()
        return {"final_answer": "ok"}

    await AutomationScheduler(store, runner).run_now(a.id)
    assert seen["effort"] == "low" and current_effort() is None
    with pytest.raises(AutomationError):
        store.create("B", "task", effort="extreme", now=NOON)
