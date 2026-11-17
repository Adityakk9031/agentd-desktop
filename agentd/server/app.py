"""Main FastAPI application for AI Operations Assistant."""

import asyncio
import hmac
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables first before importing agents/services
load_dotenv()

from agentd.services.workflow_service import WorkflowService
from agentd.safety.audit_log import AuditLog

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# httpx logs every request URL at INFO. Telegram puts the bot token in the URL
# path, so INFO-level httpx logging would write the token to the log. Keep
# the HTTP client libraries at WARNING.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Single source of truth for the app version
from agentd import __version__ as APP_VERSION  # noqa: E402

# Approval queue + channel manager are built at startup (lifespan) so the
# memory callback and channel tokens are wired once.
approval_queue: Optional["ApprovalQueue"] = None
channel_manager: Optional["ChannelManager"] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start configured chat channels (Telegram / Discord / Slack); stop on exit."""
    global approval_queue, channel_manager

    from agentd.services.approval_queue import ApprovalQueue
    from agentd.channels import build_channel_manager
    approval_queue = ApprovalQueue()
    channel_manager = build_channel_manager(workflow_service, approval_queue)
    await channel_manager.start_all()

    async def run_automation_task(task: str, mode: str):
        from agentd.safety.gate import ActionGate

        # Nobody is watching a scheduled run: it may read and look things up, but
        # anything that would change the computer is declined and reported.
        gate = ActionGate(interactive=False, audit_log=audit_log)
        if mode == "team":
            return await workflow_service.run_team(task, gate=gate)
        return await workflow_service.run_agent(task, gate=gate)

    global scheduler
    scheduler = AutomationScheduler(automation_store, run_automation_task, approval_queue)
    scheduler.start()
    active = list(channel_manager.channels.keys())
    logger.info(f"Chat channels active: {active or 'none (set TELEGRAM/DISCORD/SLACK tokens to enable)'}")

    yield

    await scheduler.stop()
    await channel_manager.stop_all()
    try:
        from agentd.computer.browser import close_browser

        await close_browser()
    except Exception:
        pass


# Initialize FastAPI app
app = FastAPI(
    title="AI Operations Assistant",
    description="Open-source agent runtime: bring your own model, connect any MCP tool, approve risky actions",
    version=APP_VERSION,
    lifespan=lifespan,
)

# CORS: configured via ALLOWED_ORIGINS (comma-separated). "*" disables
# credentials automatically — the old wildcard+credentials combo was invalid.
_origins_env = os.getenv("ALLOWED_ORIGINS", "*")
if _origins_env.strip() == "*":
    _cors_origins, _cors_credentials = ["*"], False
else:
    _cors_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API token: when AGENTD_API_TOKEN is set (the desktop app always sets a
# random one), every /api/ request must carry it. Other programs on the same
# computer then cannot drive the agent.
API_TOKEN = os.getenv("AGENTD_API_TOKEN", "")


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if API_TOKEN and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {API_TOKEN}"):
            return JSONResponse({"detail": "Missing or invalid API token"}, status_code=401)
    return await call_next(request)


# The app's screens (also used by the desktop app) are served at /app
UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

# Audit log backing GET /api/audit/stats
audit_log = AuditLog()

# Scheduled tasks; the scheduler itself starts with the app (see lifespan)
from agentd.services.automations import AutomationScheduler, AutomationStore  # noqa: E402

automation_store = AutomationStore()
scheduler: Optional[AutomationScheduler] = None

workflow_service = WorkflowService()

# Request/Response models
class TaskRequest(BaseModel):
    task: str
    mode: str = "agent"   # "agent" (one tool-using agent) or "team" (several agents)
    run_id: Optional[str] = None  # lets the app follow progress at /api/runs/{run_id}
    access: str = "ask"   # "ask": pause before changing anything; "auto": act without asking
    effort: Optional[str] = None  # "low" | "medium" | "high"; empty = the model's own default
    history: Optional[list[Dict[str, str]]] = None  # earlier turns of this conversation


class SubmitResponse(BaseModel):
    plan: Dict[str, Any]
    executor_results: list[Dict[str, Any]]
    verification: Dict[str, Any]
    needs_review: bool = False
    final_answer: Optional[str] = None
    approval_id: Optional[str] = None
    agents: Optional[list[Dict[str, Any]]] = None
    actions: Optional[list[Dict[str, Any]]] = None
    model: Optional[str] = None


@app.get("/api/status")
async def status():
    """What the app's header shows: version, models per role, active channels."""
    from agentd.doctor import run_checks
    from agentd.models.config import ModelConfigError, load_models_config

    try:
        config = load_models_config()
        checks = [c.__dict__ for c in run_checks(config)]
        source, error = config.source, None
    except ModelConfigError as exc:
        checks, source, error = [], "none", str(exc)
    return {
        "version": APP_VERSION,
        "models": checks,
        "models_source": source,
        "models_error": error,
        "channels": list(channel_manager.channels.keys()) if channel_manager else [],
        "pending_approvals": len(approval_queue.list(status="pending")) if approval_queue else 0,
    }


class ModelSettingRequest(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None
    test: bool = True


@app.get("/api/settings/catalog")
async def model_catalog(refresh: bool = False):
    """Every provider with its latest models (no key needed to browse)."""
    from agentd.models.catalog import full_catalog
    from agentd.models.config import load_models_config

    providers = await asyncio.to_thread(full_catalog, refresh)
    chain = load_models_config().chain("worker")
    return {"providers": providers, "current": chain[0].id if chain else None}


@app.get("/api/settings/providers")
async def list_providers():
    """Providers the setup form offers, and whether a key is already stored."""
    from agentd.models.config import PROVIDERS

    return {
        "providers": [
            {
                "id": name,
                "needs_key": bool(info.api_key_env),
                "has_key": any(os.getenv(e) for e in (info.api_key_env, *info.extra_key_envs) if e),
                "package": info.package,
            }
            for name, info in PROVIDERS.items()
        ]
    }


class ModelListRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None


@app.post("/api/settings/models/list")
async def list_provider_models(request: ModelListRequest):
    """Fetch the models this key can use, straight from the provider."""
    from agentd.models.catalog import list_models
    from agentd.models.config import ModelConfigError

    try:
        models = await asyncio.to_thread(list_models, request.provider, request.api_key)
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"models": models}


class ProviderKeyRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/api/settings/key")
async def set_provider_key(request: ProviderKeyRequest):
    """Check a key with the provider, store it if accepted, and return its models."""
    from agentd.models.config import ModelConfigError
    from agentd.models.settings import save_provider_key

    try:
        return await asyncio.to_thread(save_provider_key, request.provider, request.api_key)
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/settings/model")
async def set_model(request: ModelSettingRequest):
    """Test a model and, if it answers, make it the default. The key is stored, never returned."""
    from agentd.models.config import ModelConfigError
    from agentd.models.settings import save_model_setting

    try:
        return await asyncio.to_thread(
            save_model_setting, request.provider, request.model, request.api_key, None,
            **({} if request.test else {"test": False}),
        )
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/tools")
async def list_tools():
    """Connected tools and what the permission rules say about each."""
    from agentd.safety.permissions import PermissionPolicy
    from agentd.tools.langchain_tools import get_mcp_tools

    from agentd.runtime.agent import WRITE_WORDS

    policy = PermissionPolicy.from_env()
    tools = await get_mcp_tools()  # built-in and your own servers

    def permission(tool) -> str:
        meta = getattr(tool, "metadata", None) or {}
        action = policy.check(tool.name)[0]
        if meta.get("agentd_server") and action != "deny":  # same rule the agent applies
            reads_only = meta.get("readOnlyHint") is True and not meta.get("destructiveHint") and not WRITE_WORDS.search(tool.name)
            return "allow" if reads_only else "ask"
        return action

    return {
        "tools": [
            {
                "name": t.name,
                "description": (getattr(t, "description", "") or "").strip(),
                "permission": permission(t),
                "server": (getattr(t, "metadata", None) or {}).get("agentd_server"),
            }
            for t in tools
        ]
    }


@app.get("/", include_in_schema=False)
async def home():
    if UI_DIR.is_dir():
        return RedirectResponse("/app/")
    return await root()


@app.get("/api")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "AI Operations Assistant",
        "version": APP_VERSION,
        "endpoints": {
            "/api/submit": "Run a task with the agent (mode=agent) or a team of agents (mode=team)",
            "/api/audit/stats": "Counts of audited agent events by node and outcome"
        }
    }


# Progress of runs in flight, so the app can show the agent working live
RUNS: Dict[str, list] = {}
# Approval gates of runs in flight: the app answers the agent's questions here
GATES: Dict[str, Any] = {}


def _track_run(run_id: Optional[str]) -> list:
    events: list = []
    if run_id:
        RUNS[str(run_id)[:64]] = events
        while len(RUNS) > 50:  # keep only recent runs
            GATES.pop(next(iter(RUNS)), None)
            RUNS.pop(next(iter(RUNS)))
    return events


class AccessChange(BaseModel):
    access: str  # "ask" | "auto"


@app.post("/api/runs/{run_id}/access")
async def change_access(run_id: str, request: AccessChange):
    """Change how much the agent may do alone while it is working. Applies from its next action."""
    gate = GATES.get(run_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="That task is not running")
    if request.access not in ("ask", "auto"):
        raise HTTPException(status_code=400, detail="access must be 'ask' or 'auto'")
    gate.mode = request.access
    return {"ok": True, "access": gate.mode}


class LoginRequest(BaseModel):
    site: str
    username: str
    password: str


@app.get("/api/vault")
async def list_logins():
    """Saved logins, without passwords."""
    from agentd.safety.vault import Vault, VaultError

    try:
        return {"logins": Vault().list()}
    except VaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/vault")
async def save_login(request: LoginRequest):
    from agentd.safety.vault import Vault, VaultError

    try:
        return Vault().add(request.site, request.username, request.password)
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/vault/{login_id}")
async def delete_login(login_id: str):
    from agentd.safety.vault import Vault

    if not Vault().delete(login_id):
        raise HTTPException(status_code=404, detail="Login not found")
    return {"ok": True}


RESTART_EXIT_CODE = 75  # the desktop app relaunches when the runtime exits with this


@app.get("/api/update")
async def update_check(force: bool = False):
    """Is a newer release of this app on GitHub?"""
    from agentd import updates

    return await asyncio.to_thread(updates.check, force)


@app.post("/api/update/install")
async def update_install():
    """Install the newest release (only when the person presses Update), then restart."""
    from agentd import updates

    info = await asyncio.to_thread(updates.check, True)
    result = await asyncio.to_thread(updates.install, info)
    if result.get("restart"):
        async def restart_soon():
            await asyncio.sleep(1.5)  # let this answer reach the app first
            logger.info("Restarting to finish the update")
            os._exit(RESTART_EXIT_CODE)

        asyncio.get_running_loop().create_task(restart_soon())
    return result


class ConnectionRequest(BaseModel):
    name: str
    transport: str            # "stdio" | "http" | "sse"
    command: Optional[str] = None
    args: Optional[list[str]] = None
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


@app.get("/api/connections")
async def list_connections():
    """Your own MCP servers (environment and header values hidden) and how the last load went."""
    from agentd.tools import connections
    from agentd.tools.langchain_tools import SERVER_STATUS

    return {"servers": [{**s, "status": SERVER_STATUS.get(s["name"])} for s in connections.listing()]}


@app.post("/api/connections")
async def add_connection(request: ConnectionRequest):
    from agentd.tools import connections

    try:
        return connections.add(request.name, request.model_dump(exclude={"name"}, exclude_none=True))
    except connections.ConnectionError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/connections/{name}")
async def remove_connection(name: str):
    from agentd.tools import connections

    if not connections.remove(name):
        raise HTTPException(status_code=404, detail="No connection with that name")
    return {"ok": True}


class ActionDecision(BaseModel):
    decision: str  # "once" | "always" | "deny"


@app.post("/api/runs/{run_id}/actions/{action_id}")
async def answer_action(run_id: str, action_id: str, request: ActionDecision):
    """Allow or deny something the agent asked to do. The run continues at once."""
    gate = GATES.get(run_id)
    if gate is None or not gate.resolve(action_id, request.decision):
        raise HTTPException(status_code=404, detail="That question is no longer waiting for an answer")
    return {"ok": True}


@app.get("/api/runs/{run_id}")
async def run_progress(run_id: str):
    """Progress events of a team run (planning, agents starting and finishing)."""
    return {"events": RUNS.get(run_id, [])}


class AutomationRequest(BaseModel):
    name: str
    task: str
    mode: str = "single"
    kind: str = "daily"
    every_minutes: int = 60
    daily_time: str = "09:00"
    model: str = ""  # "provider:model"; empty = same model as the chat
    weekdays: list[int] = []  # daily only: 0=Mon..6=Sun; empty = every day


class AutomationToggle(BaseModel):
    enabled: bool


def _automation_json(a) -> Dict[str, Any]:
    data = a.as_dict()
    data["running"] = bool(scheduler and a.id in scheduler.running)
    return data


@app.get("/api/automations")
async def list_automations():
    return {"automations": [_automation_json(a) for a in automation_store.list()]}


@app.post("/api/automations")
async def create_automation(request: AutomationRequest):
    from agentd.services.automations import AutomationError

    try:
        return _automation_json(automation_store.create(**request.model_dump()))
    except AutomationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/api/automations/{automation_id}")
async def toggle_automation(automation_id: str, request: AutomationToggle):
    automation = automation_store.set_enabled(automation_id, request.enabled)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return _automation_json(automation)


@app.delete("/api/automations/{automation_id}")
async def delete_automation(automation_id: str):
    if not automation_store.delete(automation_id):
        raise HTTPException(status_code=404, detail="Automation not found")
    return {"ok": True}


@app.post("/api/automations/{automation_id}/run")
async def run_automation(automation_id: str):
    """Start the automation now, in the background."""
    if automation_store.get(automation_id) is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not running")
    scheduler.launch(automation_id)
    return _automation_json(automation_store.get(automation_id))


@app.post("/api/submit", response_model=SubmitResponse)
async def submit_task(request: TaskRequest, http_request: Request):
    """
    Submit a task for complete execution (Memory -> Plan -> Execute -> Verify -> SaveMemory).
    
    Orchestrated via LangGraph StateGraph workflow backed by Pinecone vector DB memory.
    
    Args:
        request: TaskRequest with task description
        
    Returns:
        SubmitResponse with plan, execution results, and verification
    """
    try:
        logger.info(f"Task ({request.mode}): '{request.task[:120]}'")
        # Stop means stop: when the app cancels the request, the run is cancelled.
        from agentd.models.config import reset_effort, set_effort
        from agentd.safety.gate import ActionGate

        # The run's tasks copy this setting when they are created just below
        effort_token = set_effort(request.effort) if request.effort is not None else None
        events = _track_run(request.run_id)
        # Without a run id nobody can answer a question, so changing actions are declined
        gate = ActionGate(mode=request.access, on_event=events.append, interactive=bool(request.run_id), audit_log=audit_log)
        if request.run_id:
            GATES[str(request.run_id)[:64]] = gate
        if request.mode == "team":
            run = asyncio.create_task(workflow_service.run_team(request.task, on_event=events.append, gate=gate))
        else:
            run = asyncio.create_task(workflow_service.run_agent(
                request.task, on_event=events.append, gate=gate, history=request.history))
        if effort_token is not None:
            reset_effort(effort_token)  # the run's own task keeps its copy
        while not run.done():
            await asyncio.wait({run}, timeout=0.5)
            if not run.done() and await http_request.is_disconnected():
                gate.cancel_all()
                run.cancel()
                logger.warning("Task stopped by the user")
                raise HTTPException(status_code=499, detail="Stopped")
        result = run.result()

        # Park results that need a human, exactly like the chat channels do
        approval_id = None
        if result.get("needs_review") and approval_queue is not None:
            approval_id = approval_queue.create(
                channel="api",
                chat_ref="api",
                task=request.task,
                plan=result.get("plan") or {},
                executor_results=result.get("executor_results") or [],
                verification=result.get("verification") or {},
                final_answer=result.get("final_answer"),
            ).id

        return SubmitResponse(
            approval_id=approval_id,
            agents=result.get("agents"),
            actions=result.get("actions"),
            model=result.get("model"),
            plan=result["plan"],
            executor_results=result["executor_results"],
            verification=result["verification"],
            needs_review=result.get("needs_review", False),
            final_answer=result.get("final_answer"),
        )
        
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Task submission failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Task submission failed: {str(e)}")



@app.get("/api/approvals")
async def list_approvals(status: str = "pending"):
    """List approvals (default: pending) — the queue behind Earned Autonomy."""
    if approval_queue is None:
        raise HTTPException(status_code=503, detail="Approval queue not initialized")
    try:
        return {
            "approvals": [a.as_dict() for a in approval_queue.list(status=status)]
        }
    except Exception as e:
        logger.error(f"Listing approvals failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Listing approvals failed: {str(e)}")


class ApprovalResolveRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    resolved_by: Optional[str] = None


@app.post("/api/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, request: ApprovalResolveRequest):
    """Approve or reject a parked result (same path the chat buttons use)."""
    if approval_queue is None:
        raise HTTPException(status_code=503, detail="Approval queue not initialized")
    if request.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    try:
        approval = approval_queue.resolve(approval_id, request.decision, request.resolved_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found or already resolved")
    return approval.as_dict()


@app.get("/api/audit/stats")
async def audit_stats():
    """Counts of audited agent events (permission checks, verifications, approvals)."""
    try:
        return audit_log.stats()
    except Exception as e:
        logger.error(f"Audit stats failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Audit stats failed: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
