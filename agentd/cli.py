"""Command-line entry point.

    agentd init              write a starter agentd.yaml
    agentd doctor [--ping]   check that each configured model will work
    agentd chat "task"       run one task end to end and print the answer
    agentd serve             run the HTTP API (localhost by default)
    agentd mcp               run the built-in tools as an MCP server (stdio)
    agentd version
"""

import argparse
import os
import sys
from pathlib import Path

from agentd import __version__

STARTER_CONFIG = """\
# agentd configuration. Keys stay in .env or your environment, never here.
#
# Write models as provider:model. Every role falls back to `default`.
# When a model fails (credit used up, bad key, outage) the next one in
# `fallbacks` takes over automatically.

models:
  default:
    model: openai:gpt-5.4-mini          # needs OPENAI_API_KEY
    fallbacks:
      - google_genai:gemini-3.5-flash   # needs GOOGLE_API_KEY or GEMINI_API_KEY
      - ollama:llama3.1                 # free, runs on this computer (ollama.com)

  # planner:  anthropic:claude-sonnet-5-5   # a stronger model just for planning
  # verifier: openai:gpt-5.4-mini

  # Memory. Must match the dimension of your vector index.
  # embeddings:
  #   model: openai:text-embedding-3-small
  #   dimensions: 768

# Connection settings per provider (optional).
# providers:
#   ollama:
#     base_url: http://localhost:11434
#   openai:                       # any OpenAI-compatible server, e.g. vLLM or LM Studio
#     base_url: http://localhost:1234/v1
#     api_key_env: LOCAL_LLM_KEY
"""


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("agentd.server.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _mcp(_: argparse.Namespace) -> int:
    from agentd.tools.mcp_server import mcp

    mcp.run(transport="stdio")
    return 0


def _init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists. Use --force to overwrite it.")
        return 1
    path.write_text(STARTER_CONFIG, encoding="utf-8")
    print(f"Wrote {path}. Edit the models, put your keys in .env, then run: agentd doctor")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from agentd.doctor import FAIL, format_report, run_checks
    from agentd.models.config import ModelConfigError, load_models_config

    try:
        config = load_models_config()
    except ModelConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    checks = run_checks(config, ping=args.ping)
    print(format_report(checks, config.source))
    usable = {c.role for c in checks if c.status != FAIL}
    return 0 if {"planner", "worker"} <= usable else 1


def _chat(args: argparse.Namespace) -> int:
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    from agentd.channels.base import format_task_reply
    from agentd.services.workflow_service import WorkflowService

    task = " ".join(args.task).strip()
    if not task:
        print("Give a task, e.g.: agentd chat \"What is the weather in Tokyo?\"")
        return 2
    from agentd.safety.gate import ActionGate

    service = WorkflowService()

    async def run():
        loop = asyncio.get_running_loop()

        def on_event(event):
            if event["type"] == "action":
                print(f"  > {event['summary']}")
            elif event["type"] == "approval":
                def ask():
                    print(f"\n  The agent wants to: {event['title']}")
                    if event.get("detail"):
                        print("    " + event["detail"][:600].replace("\n", "\n    "))
                    reply = input("  Allow? [y]es / [a]lways for this task / [n]o: ").strip().lower()
                    gate.resolve(event["id"], {"y": "once", "yes": "once", "a": "always"}.get(reply, "deny"))
                loop.run_in_executor(None, ask)

        gate = ActionGate(mode="auto" if args.yes else "ask", on_event=on_event, interactive=sys.stdin.isatty() or args.yes)
        if args.team:
            return await service.run_team(task, on_event=on_event, gate=gate)
        return await service.run_agent(task, on_event=on_event, gate=gate)

    result = asyncio.run(run())
    for agent in result.get("agents") or []:
        state = "done" if agent["success"] else f"FAILED: {agent['error']}"
        tools = ", ".join(dict.fromkeys(agent["tools_used"])) or "no tools"
        print(f"[{agent['name']} / {agent['role']}] {state} ({tools})")
    print(format_task_reply(task, result))
    if result.get("needs_review"):
        print("\n(Held for review: below the confidence threshold or blocked by a permission rule.)")
    return 0


def main(argv=None) -> int:
    # Windows consoles default to a legacy code page that cannot print emoji
    # or most non-English text; never crash on output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # Settings come from .env in the folder the command is run from.
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(prog="agentd", description="Open-source agent runtime.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Write a starter agentd.yaml")
    init.add_argument("--path", default=os.getenv("AGENTD_CONFIG", "agentd.yaml"))
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    init.set_defaults(func=_init)

    doctor = sub.add_parser("doctor", help="Check that each configured model will work")
    doctor.add_argument("--ping", action="store_true", help="Send a tiny request to each model (uses a little credit)")
    doctor.set_defaults(func=_doctor)

    chat = sub.add_parser("chat", help="Run one task and print the answer")
    chat.add_argument("task", nargs="+")
    chat.add_argument("-v", "--verbose", action="store_true", help="Show each step")
    chat.add_argument("--team", action="store_true", help="Use several agents working together")
    chat.add_argument("--yes", action="store_true", help="Let the agent change files and run commands without asking")
    chat.set_defaults(func=_chat)

    serve = sub.add_parser("serve", help="Run the HTTP API server")
    # Localhost by default: nothing is reachable from the network unless asked.
    serve.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    serve.set_defaults(func=_serve)

    mcp = sub.add_parser("mcp", help="Run the built-in tools as an MCP server over stdio")
    mcp.set_defaults(func=_mcp)

    version = sub.add_parser("version", help="Print the version")
    version.set_defaults(func=lambda _: print(__version__) or 0)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
