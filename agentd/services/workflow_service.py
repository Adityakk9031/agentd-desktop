"""Runs tasks: one tool-using agent, or a team of agents."""

import logging
from typing import Any, Dict

from agentd.tools.langchain_tools import mcp_tools_session

logger = logging.getLogger("agentd.services.workflow_service")


class WorkflowService:
    """Opens the connected tools for each task and runs the agent (or a team) with them."""

    async def run_agent(self, user_task: str, on_event=None, gate=None, history=None) -> Dict[str, Any]:
        """Run a task with the tool-using agent (files, commands, browser, connected services)."""
        from agentd.runtime.agent import Agent

        logger.info(f"Starting agent for task: '{user_task}'")
        async with mcp_tools_session() as mcp_tools:
            result = await Agent().run(user_task, list(mcp_tools), gate=gate, on_event=on_event, history=history)
        _keep(user_task, result)
        return result

    async def run_team(self, user_task: str, on_event=None, gate=None) -> Dict[str, Any]:
        """Run a task in team mode: supervisor, tool-using agents, lead."""
        from agentd.runtime.team import Team
        from agentd.safety.audit_log import AuditLog

        logger.info(f"Starting team for task: '{user_task}'")
        async with mcp_tools_session() as mcp_tools:
            tools = list(mcp_tools)
            if gate is not None:  # the team's agents can also act on this computer, through the same gate
                from agentd.computer.tools import build_computer_tools

                tools += build_computer_tools(gate)
            team = Team(audit_log=AuditLog())
            result = await team.run(user_task, tools, on_event=on_event)
        _keep(user_task, result)
        return result


def _keep(task: str, result: Dict[str, Any]) -> None:
    """Save a finished task and its answer so later tasks can recall it."""
    if "model" in result and not result["model"]:  # the agent failed before any model answered
        return
    try:
        from agentd.runtime.recall import save_conversation

        save_conversation(task, result.get("final_answer") or "")
    except Exception as exc:
        logger.warning(f"Could not save the conversation for recall: {exc}")
