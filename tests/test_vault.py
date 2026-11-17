"""Login vault, secret scrubbing, live approval-level changes and weekday routines."""

import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from agentd.safety.vault import Vault, VaultError, normalise_site, redact, site_matches


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_VAULT_DIR", str(tmp_path / "vault"))
    return Vault()


# ------------------------------------------------------------------ vault


def test_sites_are_normalised_and_matched_strictly():
    assert normalise_site("https://www.Amazon.in/ap/signin") == "amazon.in"
    assert site_matches("amazon.in", "https://www.amazon.in/login")
    assert site_matches("amazon.in", "https://pay.amazon.in/x")
    assert not site_matches("amazon.in", "https://amazon.in.evil.com/login")
    assert not site_matches("amazon.in", "https://notamazon.in/")
    with pytest.raises(VaultError):
        normalise_site("not a site")


def test_logins_are_encrypted_on_disk_and_listed_without_passwords(vault):
    vault.add("github.com", "sam", "hunter2-very-secret")
    assert vault.list() == [{"id": vault.list()[0]["id"], "site": "github.com", "username": "sam"}]
    raw = vault.data_file.read_bytes()
    assert b"hunter2" not in raw and b"sam" not in raw
    assert vault.find_for("https://github.com/login")["password"] == "hunter2-very-secret"
    assert vault.find_for("https://gitlab.com/") is None


def test_saving_again_replaces_and_delete_removes(vault):
    first = vault.add("github.com", "sam", "one")
    vault.add("github.com", "sam", "two")
    assert len(vault.list()) == 1 and vault.find_for("https://github.com")["password"] == "two"
    assert vault.delete(vault.list()[0]["id"]) is True and vault.list() == []
    assert vault.delete(first["id"]) is False


def test_a_wrong_key_cannot_open_the_vault(vault):
    vault.add("github.com", "sam", "pw12345")
    vault.key_file.write_bytes(b"A" * 43 + b"=")
    with pytest.raises(VaultError):
        vault.list()


def test_redact_hides_saved_passwords():
    assert redact("your password hunter22 was saved", ["hunter22"]) == "your password «hidden» was saved"


# ------------------------------------------------------- browser sign-in

pytest.importorskip("playwright")
from agentd.computer import browser  # noqa: E402
from agentd.safety.gate import ActionGate  # noqa: E402

LOGIN_PAGE = """<html><head><title>Sign in</title></head><body>
<input id="u" name="email" placeholder="Email"><input id="p" type="password" name="password" placeholder="Password">
<button>Sign in</button><p>Echo: <span id="echo"></span></p></body></html>"""


@pytest_asyncio.fixture
async def page(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("AGENTD_BROWSER_HEADLESS", "1")
    try:
        current = await browser.session().page()
    except RuntimeError as exc:
        pytest.skip(f"no browser available: {exc}")
    yield current
    await browser.close_browser()


async def on_site(page, url):
    """Show the login form at a given address without going to the internet."""
    await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=LOGIN_PAGE))
    await page.goto(url)


def refs(listing):
    def ref(label):
        line = next(line for line in listing.splitlines() if label in line)
        return int(line[1:line.index("]")])
    return ref


@pytest.mark.asyncio
async def test_saved_login_is_typed_without_reaching_the_agent(vault, page):
    vault.add("example.com", "sam@example.com", "S3cret-Pass!")
    await on_site(page, "https://www.example.com/login")
    tools = {t.name: t for t in browser.build_browser_tools(ActionGate(mode="auto"))}
    ref = refs(await tools["browser_read"].ainvoke({}))

    result = await tools["browser_fill_login"].ainvoke({"username_ref": ref("Email"), "password_ref": ref("Password")})

    assert "S3cret-Pass!" not in result and "sam@example.com" in result
    assert await page.input_value("#u") == "sam@example.com"
    assert await page.input_value("#p") == "S3cret-Pass!"
    # even if the site echoes the password onto the page, the agent sees it hidden
    await page.evaluate("document.getElementById('echo').textContent = 'S3cret-Pass!'")
    assert "S3cret-Pass!" not in await tools["browser_read"].ainvoke({})


@pytest.mark.asyncio
async def test_login_is_never_filled_on_another_site_or_into_a_plain_field(vault, page):
    vault.add("example.com", "sam", "S3cret-Pass!")
    tools = {t.name: t for t in browser.build_browser_tools(ActionGate(mode="auto"))}

    await on_site(page, "https://example.com.attacker.net/login")
    ref = refs(await tools["browser_read"].ainvoke({}))
    result = await tools["browser_fill_login"].ainvoke({"username_ref": ref("Email"), "password_ref": ref("Password")})
    assert "No login is saved" in result and await page.input_value("#p") == ""

    await on_site(page, "https://example.com/login")
    ref = refs(await tools["browser_read"].ainvoke({}))
    swapped = await tools["browser_fill_login"].ainvoke({"username_ref": ref("Password"), "password_ref": ref("Email")})
    assert swapped.startswith("BLOCKED") and await page.input_value("#u") == ""


@pytest.mark.asyncio
async def test_signing_in_asks_in_ask_mode(vault, page):
    vault.add("example.com", "sam", "S3cret-Pass!")
    await on_site(page, "https://example.com/login")
    events = []
    gate = ActionGate(on_event=events.append)
    tools = {t.name: t for t in browser.build_browser_tools(gate)}
    ref = refs(await tools["browser_read"].ainvoke({}))

    async def deny():
        while not gate.pending:
            await asyncio.sleep(0.01)
        gate.resolve(next(iter(gate.pending)), "deny")

    asyncio.create_task(deny())
    result = await tools["browser_fill_login"].ainvoke({"username_ref": ref("Email"), "password_ref": ref("Password")})
    assert "did not allow" in result and await page.input_value("#p") == ""
    assert events[0]["title"] == "Sign in to example.com as sam"


# ------------------------------------------------------------------ memory


@pytest.mark.parametrize("note", [
    "my OpenAI key is sk-proj-ABCDEFGHIJKLMNOPQRSTUV",
    "password: hunter2",
    "wifi pin is 4821",
    "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
])
def test_memory_never_stores_secrets(note):
    from agentd.computer.tools import scrub_secrets

    cleaned = scrub_secrets(note)
    assert "«redacted»" in cleaned
    for secret in ("ABCDEFGHIJKLMNOP", "hunter2", "4821", "ghp_"):
        assert secret not in cleaned


def test_ordinary_facts_are_remembered_unchanged():
    from agentd.computer.tools import scrub_secrets

    assert scrub_secrets("The user's name is Sam and they live in Pune") == "The user's name is Sam and they live in Pune"


# ------------------------------------------------- approval level mid-task


@pytest.mark.asyncio
async def test_raising_access_mid_task_applies_to_the_next_action():
    gate = ActionGate(mode="ask", on_event=lambda e: None)
    asked = asyncio.create_task(gate.request("write", "first"))
    await asyncio.sleep(0.05)
    assert gate.pending  # waiting for an answer

    gate.mode = "auto"   # the user switches to Full access while it waits
    gate.resolve(next(iter(gate.pending)), "once")
    assert await asked is True
    assert await gate.request("write", "second") is True and not gate.pending


# ---------------------------------------------------------------- routines


def test_weekday_routines_skip_the_weekend(tmp_path):
    from agentd.services.automations import AutomationStore

    store = AutomationStore(db_path=str(tmp_path / "a.db"))
    friday_evening = datetime(2026, 11, 20, 18, 0)  # a Friday
    a = store.create("Standup", "brief", kind="daily", daily_time="09:00", weekdays=[0, 1, 2, 3, 4], now=friday_evening)
    assert a.next_run_at == "2026-11-23T09:00:00"  # Monday
    assert a.as_dict()["schedule"] == "Every weekday at 09:00"


def test_a_run_missed_by_more_than_12_hours_is_skipped_not_fired(tmp_path):
    from agentd.services.automations import AutomationStore

    store = AutomationStore(db_path=str(tmp_path / "a.db"))
    start = datetime(2026, 11, 16, 8, 0)
    a = store.create("Brief", "brief", kind="daily", daily_time="09:00", now=start)

    assert [x.id for x in store.due(now=start + timedelta(hours=5))] == [a.id]  # 4 hours late: still runs
    late = store.due(now=start + timedelta(days=2))                            # 47 hours late
    assert late == []
    skipped = store.get(a.id)
    assert skipped.last_status == "skipped" and skipped.next_run_at > (start + timedelta(days=2)).isoformat()
