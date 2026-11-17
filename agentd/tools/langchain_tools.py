"""MCP tool utilities — ONE shared stdio session per request.

The WorkflowService opens a single session around the whole graph run and
passes the loaded tools through graph state, so nodes never spawn their own
subprocesses.
"""

import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

logger = logging.getLogger("agentd.tools.mcp_tools")

# The built-in tool server runs as a module of this package. cwd is the
# directory that contains the `agentd` package, so it also starts from a
# source checkout that has not been pip-installed.
PACKAGE_PARENT = Path(__file__).resolve().parents[2]

server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "agentd.tools.mcp_server"],
    cwd=str(PACKAGE_PARENT),
)


@asynccontextmanager
async def mcp_tools_session():
    """
    Open one MCP ClientSession over stdio and yield the loaded tools.

    Yields [] when the server cannot be started, so the pipeline can fall back
    to direct tool instances. Errors raised by the *caller* inside the
    ``async with`` block propagate unchanged (they are not swallowed or turned
    into a second yield).
    """
    stack = AsyncExitStack()
    tools: List[Any] = []
    try:
        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = await load_mcp_tools(session)
        logger.info(f"Loaded {len(tools)} tools from MCP server (shared session)")
    except Exception as exc:
        logger.error(f"Failed to load MCP tools from server: {exc}")
        tools = []
        try:
            await stack.aclose()
        except Exception as close_exc:  # pragma: no cover - best-effort cleanup
            logger.warning(f"Error closing failed MCP session: {close_exc}")

    try:
        yield tools
    finally:
        try:
            await stack.aclose()
        except Exception as close_exc:  # pragma: no cover - best-effort cleanup
            logger.warning(f"Error closing MCP session: {close_exc}")


async def get_mcp_tools() -> List[Any]:
    """Load the tool list (names, descriptions, schemas) and close the session.

    The returned tools are only good for describing tools to the planner; they
    cannot be invoked after the session closes.
    """
    async with mcp_tools_session() as tools:
        return list(tools)
