"""Parts adapted from OpenClaw: loop guard, skills, web search, user MCP servers, browser snapshots."""

import asyncio
import json
import sys
import textwrap

import pytest
import pytest_asyncio
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agentd.models.config import ModelsConfig, RoleConfig, parse_spec
from agentd.runtime.agent import REPEAT_STOP, Agent, LoopGuard
from agentd.safety.gate import ActionGate
from agentd.safety.permissions import PermissionPolicy

CONFIG = ModelsConfig(roles={"default": RoleConfig(parse_spec("fake:one"))})


class ToolModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(tool_name, i=0, **args):
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": f"c{i}"}])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("AGENTD_MEMORY_FILE", str(tmp_path / "memory.md"))
    monkeypatch.setenv("AGENTD_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("AGENTD_MCP_SERVERS", str(tmp_path / "mcp.json"))
    return tmp_path


# ------------------------------------------------------------ loop guard


@pytest.mark.asyncio
async def test_loop_guard_warns_then_stops():
    calls = []

    async def ping(x: str) -> str:
        """Ping."""
        calls.append(x)
        return "pong"

    guard = LoopGuard()
    tool = guard.wrap(StructuredTool.from_function(coroutine=ping, name="ping", description="Ping."))

    assert await tool.ainvoke({"x": "a"}) == "pong"
    assert await tool.ainvoke({"x": "a"}) == "pong"
    assert "call 3 of ping" in await tool.ainvoke({"x": "a"})
    assert await tool.ainvoke({"x": "b"}) == "pong"  # a different call resets the count
    for _ in range(REPEAT_STOP - 1):
        await tool.ainvoke({"x": "c"})
    stopped = await tool.ainvoke({"x": "c"})
    assert stopped.startswith("I stopped") and guard.stopped
    assert calls.count("c") == REPEAT_STOP - 1  # the stopping call never ran


@pytest.mark.asyncio
async def test_agent_ends_the_task_when_the_loop_guard_stops(home):
    (home / "a.txt").write_text("x", encoding="utf-8")
    script = [call("read_file", i, path="a.txt") for i in range(REPEAT_STOP + 5)]
    model = ToolModel(messages=iter(script))
    agent = Agent(permissions=PermissionPolicy(), config=CONFIG, model_factory=lambda spec: model)

    result = await agent.run("read a.txt forever", gate=ActionGate(mode="auto"))

    assert result["final_answer"].startswith("I stopped") and "read_file" in result["final_answer"]
    assert len(result["actions"]) <= REPEAT_STOP + 1


# ---------------------------------------------------------------- skills


def test_bundled_and_user_skills_load_and_user_wins(home):
    from agentd.runtime.skills import load_skills, skills_prompt

    custom = home / "skills" / "online-shopping"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text("---\nname: online-shopping\ndescription: My own way\n---\nUse my shop.", encoding="utf-8")

    skills = load_skills()
    assert "web-research" in skills  # bundled
    assert skills["online-shopping"]["body"] == "Use my shop." and skills["online-shopping"]["description"] == "My own way"
    assert "- web-research:" in skills_prompt(skills)


@pytest.mark.asyncio
async def test_the_agent_sees_skill_names_and_reads_one_on_demand(home):
    seen = {}

    class Recorder(ToolModel):
        def _generate(self, messages, *args, **kwargs):
            seen.setdefault("system", messages[0].content)
            seen.setdefault("tools", None)
            return super()._generate(messages, *args, **kwargs)

    model = Recorder(messages=iter([call("use_skill", name="web-research"), AIMessage(content="done")]))
    result = await Agent(permissions=PermissionPolicy(), config=CONFIG, model_factory=lambda spec: model).run("research x")

    assert "web-research" in seen["system"] and "use_skill" in seen["system"]
    assert result["actions"][0]["tool"] == "use_skill" and result["actions"][0]["ok"]
    assert "web_search" in result["actions"][0]["preview"]  # the skill body came back


# ------------------------------------------------------------- web search

DDG = """
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example%2Fshoes&amp;rut=x">Cheap <b>running</b> shoes</a>
  <a class="result__snippet" href="#">Shoes from <b>499</b> with free delivery</a>
</div>
<div class="result results_links results_links_deep result--ad">
  <a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Advert</a>
</div>
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a" href="https://reviews.example/best">Best shoes 2026</a>
  <a class="result__snippet" href="#">Our picks</a>
</div>"""


def test_search_results_are_parsed_without_adverts():
    from agentd.computer.tools import parse_search_results

    results = parse_search_results(DDG)
    assert results == [
        {"title": "Cheap running shoes", "url": "https://shop.example/shoes", "snippet": "Shoes from 499 with free delivery"},
        {"title": "Best shoes 2026", "url": "https://reviews.example/best", "snippet": "Our picks"},
    ]


# ------------------------------------------------------- your MCP servers

SERVER = textwrap.dedent('''
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    mcp = FastMCP("notes")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_notes() -> str:
        """List the notes."""
        return "groceries, ideas"

    @mcp.tool()
    def add_note(text: str) -> str:
        """Add a note."""
        return "added " + text

    mcp.run(transport="stdio")
''')


def test_connection_settings_are_checked_and_secrets_not_shown(home):
    from agentd.tools import connections

    with pytest.raises(connections.ConnectionError_):
        connections.add("Bad Name!", {"transport": "stdio", "command": "x"})
    with pytest.raises(connections.ConnectionError_):
        connections.add("web", {"transport": "http", "url": "ftp://x"})
    connections.add("notes", {"transport": "stdio", "command": "python", "args": "server.py --x", "env": {"TOKEN": "s3cret"}})

    listed = connections.listing()
    assert listed == [{"name": "notes", "transport": "stdio", "command": "python", "args": ["server.py", "--x"], "env": ["TOKEN"]}]
    assert "s3cret" not in json.dumps(listed)
    assert connections.remove("notes") is True and connections.listing() == []


@pytest.mark.asyncio
async def test_a_real_user_server_loads_and_only_its_read_tool_runs_without_asking(home):
    from agentd.tools import connections
    from agentd.tools.langchain_tools import SERVER_STATUS, mcp_tools_session

    script = home / "notes_server.py"
    script.write_text(SERVER, encoding="utf-8")
    connections.add("notes", {"transport": "stdio", "command": sys.executable, "args": [str(script)]})
    connections.add("broken", {"transport": "stdio", "command": "no-such-program-xyz"})

    events = []
    async with mcp_tools_session() as tools:
        names = {t.name for t in tools}
        assert {"list_notes", "add_note", "github_search_repos"} <= names  # built-in tools still there
        model = ToolModel(messages=iter([call("list_notes", 1), call("add_note", 2, text="milk"), AIMessage(content="ok")]))
        agent = Agent(permissions=PermissionPolicy(), config=CONFIG, model_factory=lambda spec: model)
        result = await agent.run("notes", connected_tools=tools,
                                 gate=ActionGate(interactive=False, on_event=events.append))

    assert SERVER_STATUS["notes"]["ok"] and not SERVER_STATUS["broken"]["ok"]  # one broken server does not stop the rest
    read, write = result["actions"]
    assert read["ok"] and "groceries" in read["preview"]
    assert write["ok"] is False and "declined" in write["preview"]  # writing needs a yes; nobody was there


# ------------------------------------------------------- browser snapshot

# The browser tests below skip themselves when Playwright is missing; the rest always run.
from agentd.computer import browser  # noqa: E402

PAGE = """<html><body><h1>Shop</h1>
<input aria-label="Password" type="password" name="password">
<input aria-label="Card number" name="cardnumber">
<input aria-label="Search" name="q">
<button onclick="document.body.insertAdjacentHTML('beforeend','<button>Checkout</button>')">Add to cart</button>
</body></html>"""


@pytest_asyncio.fixture
async def page(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("AGENTD_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("AGENTD_BROWSER_HEADLESS", "1")
    try:
        current = await browser.session().page()
    except RuntimeError as exc:
        pytest.skip(f"no browser available: {exc}")
    await current.set_content(PAGE)
    yield current
    await browser.close_browser()


@pytest.mark.asyncio
async def test_values_typed_by_the_user_in_secret_boxes_are_never_shown(page):
    await page.fill("input[name=password]", "typed-by-user-123")
    await page.fill("input[name=cardnumber]", "4111111111111111")
    await page.fill("input[name=q]", "running shoes")
    tools = {t.name: t for t in browser.build_browser_tools(ActionGate(mode="auto"))}

    listing = await tools["browser_read"].ainvoke({})

    assert "typed-by-user-123" not in listing and "4111111111111111" not in listing
    assert listing.count("«hidden»") == 2 and "running shoes" in listing


@pytest.mark.asyncio
async def test_elements_that_appear_after_an_action_are_marked_new(page):
    import re

    tools = {t.name: t for t in browser.build_browser_tools(ActionGate(mode="auto"))}
    listing = await tools["browser_read"].ainvoke({})
    assert "[new]" not in listing  # nothing is new on the first look
    add = re.search(r'button "Add to cart" \[ref=(e\d+)\]', listing).group(1)

    after = await tools["browser_click"].ainvoke({"ref": add})

    assert re.search(r'button "Checkout" \[ref=e\d+\].*\[new\]', after)
    assert "1 new element(s)" in after
    assert not re.search(r'Add to cart" .*\[new\]', after)  # what was already there is not marked
