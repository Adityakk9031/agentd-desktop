"""Static wiring checks: the screens only use page elements and server routes that exist.

These read the UI files as text, so they catch a renamed id or endpoint
without starting a browser.
"""

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "agentd" / "server" / "ui"
JS = (UI / "app.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "styles.css").read_text(encoding="utf-8")
SERVER = (UI.parent / "app.py").read_text(encoding="utf-8")


def test_every_element_the_code_uses_exists_on_the_page():
    used = set(re.findall(r'\$\("([\w-]+)"\)', JS))
    present = set(re.findall(r'\bid="([\w-]+)"', HTML))
    assert used - present == set(), f"app.js uses ids missing from index.html: {sorted(used - present)}"


def test_every_view_the_menu_opens_exists():
    views = set(re.findall(r'data-view="([\w-]+)"', HTML))
    sections = set(re.findall(r'id="view-([\w-]+)"', HTML))
    assert views <= sections, f"menu points at missing views: {sorted(views - sections)}"
    loaders = set(re.findall(r"(\w+): load\w+", JS[JS.index("const loaders"):JS.index("const loaders") + 200]))
    assert loaders <= sections


def _server_routes():
    routes = set()
    for method, path in re.findall(r'@app\.(get|post|patch|delete)\("([^"]+)"', SERVER):
        routes.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", path)))
    return routes


def test_every_request_the_code_sends_has_a_server_route():
    routes = _server_routes()
    calls = set()
    # api("/path", { method: "POST" ... }) and api(`/path/${id}/x`, ...)
    for match in re.finditer(r'api\(\s*([`"])(/api/[^`"]*)\1([^;]*?)\)\s*[;,)\n]', JS, re.S):
        path = re.sub(r"\$\{[^}]+\}", "{}", match.group(2)).split("?")[0]
        method = re.search(r'method:\s*"(\w+)"', match.group(3))
        calls.add(((method.group(1) if method else "GET"), path))
    # paths built with string concatenation, e.g. "/api/settings/catalog" + (...)
    for path in re.findall(r'api\("(/api/[\w/]+)"\s*\+', JS):
        calls.add(("GET", path))
    assert calls, "no API calls found; the pattern needs updating"
    missing = {c for c in calls if c not in routes}
    assert missing == set(), f"app.js calls routes the server does not have: {sorted(missing)}"


def test_page_loads_only_local_files():
    # The content-security policy forbids anything else; keep it that way.
    assert "default-src 'self'" in HTML
    assert not re.search(r'(src|href)="https?://', HTML)
    assert "innerHTML" not in JS, "agent text must be inserted as text, never as HTML"


def test_stylesheet_is_balanced():
    assert CSS.count("{") == CSS.count("}")


def test_markdown_renderer_never_inserts_raw_html():
    md = (UI / "markdown.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in md.splitlines() if not line.strip().startswith("//"))
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert unsafe not in code, unsafe
    assert 'SAFE_LINK = /^(https?:\/\/|mailto:)/i' in md  # only web and mail links become links
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert html.index('src="markdown.js"') < html.index('src="app.js"')


def test_board_styles_do_not_leak_onto_chat_messages():
    """Team-board rows once shared the .agent class with every agent message and squeezed answers to 18px."""
    for line in CSS.splitlines():
        rule = line.split("{")[0]
        for selector in rule.split(","):
            selector = selector.strip()
            if re.match(r"^\.agent\b", selector):
                raise AssertionError(f"unscoped .agent rule: {selector}")
