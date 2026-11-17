"""Offline fakes shared by the unit test suite. No network, no API keys."""

import json
from typing import Any, Dict, List, Optional

class FakeMCPTool:
    """MCP tool stand-in that behaves like the real langchain-mcp-adapters tool.

    Real adapter tools are async-only (sync ``invoke`` raises
    NotImplementedError) and return MCP content blocks whose text is the JSON
    envelope ``{ok, status_code, data | error}``. The fake does the same, so a
    test that passes here also passes against a real MCP session.
    """

    def __init__(self, name: str, result: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None, tool_error: Optional[str] = None):
        self.name = name
        self.result = result if result is not None else {"ok": True}
        self.error = error            # exception raised by the call
        self.tool_error = tool_error  # tool returned {"ok": false, "error": ...}
        self.invocations: List[Dict[str, Any]] = []

    def invoke(self, inputs: Dict[str, Any]) -> Any:
        raise NotImplementedError("StructuredTool does not support sync invocation.")

    async def ainvoke(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.invocations.append(inputs)
        if self.error:
            raise RuntimeError(self.error)
        if self.tool_error:
            envelope = {"ok": False, "status_code": None, "error": self.tool_error}
        else:
            envelope = {"ok": True, "status_code": 200, "data": self.result}
        return [{"type": "text", "text": json.dumps(envelope)}]
