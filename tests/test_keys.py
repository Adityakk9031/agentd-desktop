"""API keys go to the OS keychain, never a plain text file (a fake keychain stands in for the real one)."""

import pytest

keyring = pytest.importorskip("keyring")
from keyring.backend import KeyringBackend  # noqa: E402

from agentd.safety import keys  # noqa: E402


class FakeKeychain(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture
def keychain(monkeypatch):
    fake = FakeKeychain()
    previous = keyring.get_keyring()
    keyring.set_keyring(fake)
    monkeypatch.setenv("AGENTD_KEYCHAIN", "1")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    yield fake
    keyring.set_keyring(previous)


def test_a_key_goes_to_the_keychain_and_leaves_the_env_file(keychain, tmp_path):
    env = tmp_path / ".env"
    env.write_text("XAI_API_KEY=old-plain-text\nPORT=8000\n", encoding="utf-8")

    where = keys.store_key("XAI_API_KEY", "xai-new-key-123", env)

    assert where == "keychain" and keychain.store[("agentd-keys", "XAI_API_KEY")] == "xai-new-key-123"
    assert env.read_text(encoding="utf-8") == "PORT=8000\n"  # no copy left in plain text


def test_stored_keys_load_at_start_but_a_key_set_by_hand_wins(keychain, monkeypatch):
    keychain.store[("agentd-keys", "XAI_API_KEY")] = "from-keychain"
    keychain.store[("agentd-keys", "OPENAI_API_KEY")] = "openai-from-keychain"
    monkeypatch.setenv("OPENAI_API_KEY", "set-by-hand")

    loaded = keys.load_keys(["XAI_API_KEY", "OPENAI_API_KEY"])

    import os
    assert loaded == ["XAI_API_KEY"] and os.environ["XAI_API_KEY"] == "from-keychain"
    assert os.environ["OPENAI_API_KEY"] == "set-by-hand"


def test_without_a_keychain_the_key_goes_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTD_KEYCHAIN", "0")
    env = tmp_path / ".env"
    assert keys.store_key("XAI_API_KEY", "xai-file-key", env) == "env-file"
    assert "XAI_API_KEY=xai-file-key" in env.read_text(encoding="utf-8")
    assert keys.load_keys() == []


def test_a_broken_keychain_falls_back_to_the_file(keychain, monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("locked")

    monkeypatch.setattr(keychain, "set_password", broken)
    env = tmp_path / ".env"
    assert keys.store_key("XAI_API_KEY", "xai-k", env) == "env-file"
    assert "XAI_API_KEY=xai-k" in env.read_text(encoding="utf-8")


def test_saving_a_key_from_the_app_uses_the_keychain(keychain, tmp_path):
    from agentd.models.settings import save_provider_key

    env = tmp_path / ".env"
    result = save_provider_key("xai", "xai-checked-key", env_path=env, lister=lambda provider, key: ["grok-4.6"])
    assert result["ok"] and keychain.store[("agentd-keys", "XAI_API_KEY")] == "xai-checked-key"
    assert not env.exists() or "xai-checked-key" not in env.read_text(encoding="utf-8")


def test_every_provider_key_name_is_known():
    names = keys.key_names()
    assert {"OPENAI_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY"} <= set(names)
