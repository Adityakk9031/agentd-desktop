"""The real server, driven the way the app drives it (no model, no network).

The FastAPI app is started in a throwaway folder with chat channels and
cloud memory switched off, and the workflow replaced by a fake. Everything
else is the real code: routes, token check, approval queue, automations,
scheduler, run progress.
"""

import asyncio
import importlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

REVIEW_RESULT = {
    "plan": {"task_summary": "Risky thing", "steps": []},
    "executor_results": [],
    "verification": {"source": "local", "confidence": 0.4, "issues": [], "final_output": {"summary": "Draft"}},
    "needs_review": True,
    "final_answer": "A drafted answer",
}
OK_RESULT = {**REVIEW_RESULT, "needs_review": False, "final_answer": "All done",
             "verification": {**REVIEW_RESULT["verification"], "confidence": 0.95}}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("server")
    patch = pytest.MonkeyPatch()
    patch.chdir(folder)
    # Empty (not missing) so the project's real .env cannot switch them back on
    for name in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_APP_TOKEN", "PINECONE_API_KEY", "AGENTD_API_TOKEN"):
        patch.setenv(name, "")
    patch.setenv("AGENTD_MODEL_DEFAULT", "ollama:test-model")
    sys.modules.pop("agentd.server.app", None)
    module = importlib.import_module("agentd.server.app")

    calls = []

    async def fake_task(task):
        calls.append(("single", task))
        if "slow" in task:
            await asyncio.sleep(30)
        return dict(REVIEW_RESULT if "risky" in task else OK_RESULT)

    async def fake_team(task, on_event=None):
        calls.append(("team", task))
        if on_event:
            on_event({"type": "planning"})
            on_event({"type": "planned", "summary": task, "agents": [{"id": 1, "name": "A", "role": "r", "task": task, "tools": []}]})
            on_event({"type": "agent_done", "id": 1, "success": True, "error": None, "tools_used": [], "model": "x"})
        return {**OK_RESULT, "agents": [{"id": 1, "name": "A", "role": "r", "task": task, "success": True,
                                         "output": "report", "error": None, "tools_used": [], "model": "x"}]}

    async def fake_agent(task, on_event=None, gate=None, history=None):
        calls.append(("agent", task, len(history or [])))
        if "write" in task:  # the agent asks before changing something
            allowed = await gate.request("write", "Create file notes.txt", "hello")
            return {**OK_RESULT, "final_answer": "written" if allowed else "declined",
                    "actions": [{"tool": "write_file", "summary": "Writing notes.txt", "ok": allowed, "preview": ""}]}
        return await fake_task(task)

    async def fake_team_gated(task, on_event=None, gate=None):
        return await fake_team(task, on_event)

    patch.setattr(module.workflow_service, "run_agent", fake_agent)
    patch.setattr(module.workflow_service, "run_team", fake_team_gated)
    with TestClient(module.app) as client:
        yield client, module, calls
    patch.undo()
    sys.modules.pop("agentd.server.app", None)


def test_app_page_and_health(server):
    client, _, _ = server
    assert client.get("/health").json() == {"status": "healthy"}
    page = client.get("/app/")
    assert page.status_code == 200 and "<title>agentd</title>" in page.text
    assert client.get("/app/app.js").status_code == 200 and client.get("/app/styles.css").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_status_reports_models_and_counts(server):
    client, _, _ = server
    status = client.get("/api/status").json()
    assert status["version"] and status["channels"] == []
    assert any(m["model"] == "ollama:test-model" for m in status["models"])


def test_single_task_round_trip(server):
    client, _, calls = server
    reply = client.post("/api/submit", json={"task": "say hi"}).json()
    assert reply["final_answer"] == "All done" and reply["needs_review"] is False and reply["approval_id"] is None
    assert ("agent", "say hi", 0) in calls


def test_team_task_returns_agents_and_live_progress(server):
    client, _, calls = server
    reply = client.post("/api/submit", json={"task": "big job", "mode": "team", "run_id": "run-1"}).json()
    assert reply["agents"][0]["output"] == "report"
    events = client.get("/api/runs/run-1").json()["events"]
    assert [e["type"] for e in events] == ["planning", "planned", "agent_done"]
    assert client.get("/api/runs/unknown").json() == {"events": []}
    assert ("team", "big job") in calls


def test_review_result_is_parked_then_approved(server):
    client, _, _ = server
    reply = client.post("/api/submit", json={"task": "risky thing"}).json()
    approval_id = reply["approval_id"]
    assert reply["needs_review"] is True and approval_id

    pending = client.get("/api/approvals?status=pending").json()["approvals"]
    assert [a["id"] for a in pending] == [approval_id] and pending[0]["channel"] == "api"
    assert client.get("/api/status").json()["pending_approvals"] == 1

    assert client.post(f"/api/approvals/{approval_id}/resolve", json={"decision": "maybe"}).status_code == 400
    done = client.post(f"/api/approvals/{approval_id}/resolve", json={"decision": "approved", "resolved_by": "test"}).json()
    assert done["status"] == "approved"
    assert client.get("/api/approvals?status=pending").json()["approvals"] == []
    assert client.post(f"/api/approvals/{approval_id}/resolve", json={"decision": "rejected"}).status_code == 404


def test_automation_lifecycle_runs_through_the_scheduler(server):
    client, _, calls = server
    bad = client.post("/api/automations", json={"name": "", "task": "x"})
    assert bad.status_code == 400

    created = client.post("/api/automations", json={
        "name": "Brief", "task": "morning brief", "mode": "team", "kind": "interval", "every_minutes": 60,
        "model": "xai:grok-4.7"}).json()
    assert created["schedule"] == "Every hour" and created["model"] == "xai:grok-4.7" and created["running"] is False

    assert client.post(f"/api/automations/{created['id']}/run").status_code == 200
    for _ in range(50):  # the run happens in the background
        current = client.get("/api/automations").json()["automations"][0]
        if current["last_status"]:
            break
        time.sleep(0.1)
    assert current["last_status"] == "ok" and current["last_result"] == "All done"
    assert ("team", "morning brief") in calls

    paused = client.patch(f"/api/automations/{created['id']}", json={"enabled": False}).json()
    assert paused["enabled"] is False and paused["next_run_at"] is None
    assert client.delete(f"/api/automations/{created['id']}").json() == {"ok": True}
    assert client.get("/api/automations").json()["automations"] == []
    assert client.delete(f"/api/automations/{created['id']}").status_code == 404


def test_settings_reject_bad_input_without_calling_any_provider(server):
    client, _, _ = server
    assert client.post("/api/settings/models/list", json={"provider": "nope"}).status_code == 400
    assert client.post("/api/settings/key", json={"provider": "openai", "api_key": ""}).status_code == 400
    assert client.post("/api/settings/model", json={"provider": "nope", "model": "x"}).status_code == 400
    providers = {p["id"]: p for p in client.get("/api/settings/providers").json()["providers"]}
    assert providers["openai"]["needs_key"] is True and providers["ollama"]["needs_key"] is False


def test_token_is_required_once_set(server, monkeypatch):
    client, module, _ = server
    monkeypatch.setattr(module, "API_TOKEN", "secret-token")
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer secret-token"}).status_code == 200
    assert client.get("/health").status_code == 200   # health and the page itself stay open
    assert client.get("/app/").status_code == 200


def test_conversation_history_reaches_the_agent(server):
    client, _, calls = server
    history = [{"role": "user", "content": "my name is Sam"}, {"role": "assistant", "content": "Hi Sam"}]
    client.post("/api/submit", json={"task": "what is my name", "history": history})
    assert ("agent", "what is my name", 2) in calls


def test_agent_question_waits_for_the_users_answer(server):
    """The run pauses on an approval, the app answers it, and the run continues."""
    import threading

    client, _, _ = server
    replies = {}

    def submit(run_id, decision_key):
        replies[decision_key] = client.post("/api/submit", json={"task": "write a note", "run_id": run_id}).json()

    for run_id, decision, expected in (("run-allow", "once", "written"), ("run-deny", "deny", "declined")):
        thread = threading.Thread(target=submit, args=(run_id, decision))
        thread.start()
        question = None
        for _ in range(100):
            events = client.get(f"/api/runs/{run_id}").json()["events"]
            question = next((e for e in events if e["type"] == "approval"), None)
            if question:
                break
            time.sleep(0.05)
        assert question and question["title"] == "Create file notes.txt"
        assert thread.is_alive(), "the run must wait for an answer"
        assert client.post(f"/api/runs/{run_id}/actions/{question['id']}", json={"decision": decision}).json() == {"ok": True}
        thread.join(timeout=10)
        assert replies[decision]["final_answer"] == expected
        assert client.post(f"/api/runs/{run_id}/actions/{question['id']}", json={"decision": "once"}).status_code == 404


def test_without_a_run_id_changing_actions_are_declined(server):
    client, _, _ = server
    reply = client.post("/api/submit", json={"task": "write a note"}).json()
    assert reply["final_answer"] == "declined"


def test_full_access_does_not_ask(server):
    client, _, _ = server
    reply = client.post("/api/submit", json={"task": "write a note", "run_id": "run-auto", "access": "auto"}).json()
    assert reply["final_answer"] == "written"
    assert not any(e["type"] == "approval" for e in client.get("/api/runs/run-auto").json()["events"])
