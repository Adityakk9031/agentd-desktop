"""Offline tests for team mode (supervisor -> tool-using agents -> lead)."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel, GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agentd.models.client import LLMClient
from agentd.models.config import ModelsConfig, RoleConfig, parse_spec
from agentd.runtime.team import Team
from agentd.safety.permissions import PermissionPolicy


class ToolModel(GenericFakeChatModel):
    """Fake chat model that accepts tools (the stock fake cannot bind them)."""

    def bind_tools(self, tools, **kwargs):
        return self


def scripted(*messages):
    return ToolModel(messages=iter(messages))


def client(role, *responses):
    config = ModelsConfig(roles={"default": RoleConfig(parse_spec("fake:model"))})
    model = FakeListChatModel(responses=list(responses))
    return LLMClient(role, config, lambda spec: model)


CONFIG = ModelsConfig(roles={"default": RoleConfig(parse_spec("fake:one"), [parse_spec("fake:two")])})
calls = []


@tool
def repo_stars(name: str) -> str:
    """Look up how many stars a repository has."""
    calls.append(("repo_stars", name))
    return json.dumps({"ok": True, "data": {"name": name, "stars": 81000}})


@tool
def repo_delete(name: str) -> str:
    """Delete a repository."""
    calls.append(("repo_delete", name))
    return "deleted"


@pytest.fixture(autouse=True)
def reset_calls():
    calls.clear()


def plan(*agents):
    return json.dumps({"summary": "Test task", "agents": list(agents)})


@pytest.mark.asyncio
async def test_agent_calls_a_tool_and_reports(audit_log):
    agent_model = scripted(
        AIMessage(content="", tool_calls=[{"name": "repo_stars", "args": {"name": "django"}, "id": "c1"}]),
        AIMessage(content="django has 81000 stars."),
    )
    team = Team(
        permissions=PermissionPolicy(), audit_log=audit_log, config=CONFIG,
        supervisor=client("planner", plan({"name": "Scout", "role": "researcher", "task": "Find django stars", "tools": ["repo_stars"]})),
        lead=client("worker", '{"answer": "unused"}'),
        model_factory=lambda spec: agent_model,
    )

    result = await team.run("How many stars does django have?", [repo_stars])

    assert calls == [("repo_stars", "django")]
    assert result["final_answer"] == "django has 81000 stars."  # one agent: no lead call needed
    assert result["agents"][0]["tools_used"] == ["repo_stars"]
    assert result["needs_review"] is False
    assert audit_log.stats()["by_node"] == {"agent:Scout": 1}


@pytest.mark.asyncio
async def test_two_agents_are_combined_by_the_lead():
    models = iter([scripted(AIMessage(content="Report A")), scripted(AIMessage(content="Report B"))])
    team = Team(
        permissions=PermissionPolicy(), config=CONFIG,
        supervisor=client("planner", plan(
            {"name": "A", "role": "researcher", "task": "Job A"}, {"name": "B", "role": "writer", "task": "Job B"})),
        lead=client("worker", '{"answer": "Combined A and B"}'),
        model_factory=lambda spec: next(models),
    )

    result = await team.run("Do two things")

    assert result["final_answer"] == "Combined A and B"
    assert [a["output"] for a in result["agents"]] == ["Report A", "Report B"]
    assert len(result["plan"]["steps"]) == 2 and all(r["success"] for r in result["executor_results"])


@pytest.mark.asyncio
async def test_agents_never_receive_tools_that_need_approval():
    """Even if the supervisor asks for it, a held tool is not given to any agent."""
    handed_over = []

    class Recorder(ToolModel):
        def bind_tools(self, tools, **kwargs):
            handed_over.extend(getattr(t, "name", str(t)) for t in tools)
            return self

    team = Team(
        permissions=PermissionPolicy(),  # *delete* is on the default ask list
        config=CONFIG,
        supervisor=client("planner", plan({"name": "X", "role": "operator", "task": "Delete repo", "tools": ["repo_delete", "repo_stars"]})),
        lead=client("worker", "{}"),
        model_factory=lambda spec: Recorder(messages=iter([AIMessage(content="I cannot delete without approval.")])),
    )

    result = await team.run("Delete the repo", [repo_stars, repo_delete])

    assert "repo_delete" not in handed_over and "repo_stars" in handed_over
    assert calls == []
    assert result["verification"]["verifier_metadata"]["held_tools"] == ["repo_delete"]


@pytest.mark.asyncio
async def test_agent_falls_back_to_the_next_model():
    def factory(spec):
        if spec.id == "fake:one":
            raise RuntimeError("401 key expired")
        return scripted(AIMessage(content="Done by the fallback."))

    team = Team(
        permissions=PermissionPolicy(), config=CONFIG,
        supervisor=client("planner", plan({"name": "A", "role": "generalist", "task": "Job"})),
        lead=client("worker", "{}"), model_factory=factory,
    )

    result = await team.run("Job")

    assert result["agents"][0]["model"] == "fake:two"
    assert result["final_answer"] == "Done by the fallback."


@pytest.mark.asyncio
async def test_all_agents_failing_goes_to_review():
    def factory(spec):
        raise RuntimeError("provider down")

    team = Team(
        permissions=PermissionPolicy(), config=CONFIG,
        supervisor=client("planner", plan({"name": "A", "role": "generalist", "task": "Job"})),
        lead=client("worker", "{}"), model_factory=factory,
    )

    result = await team.run("Job")

    assert result["needs_review"] is True
    assert "provider down" in result["final_answer"]
    assert result["verification"]["issues"]


@pytest.mark.asyncio
async def test_unusable_supervisor_plan_falls_back_to_one_agent():
    team = Team(
        permissions=PermissionPolicy(), config=CONFIG,
        supervisor=client("planner", "nonsense", "still nonsense"),
        lead=client("worker", "{}"),
        model_factory=lambda spec: scripted(AIMessage(content="Handled alone.")),
    )

    result = await team.run("Something")

    assert len(result["agents"]) == 1 and result["final_answer"] == "Handled alone."
