"""Offline tests for bring-your-own-model: config, fallback client, doctor."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agentd.models.client import AllModelsFailed, LLMClient, NoModelConfigured, parse_json
from agentd.models.config import ModelConfigError, ModelsConfig, RoleConfig, load_models_config, parse_spec

ENV_VARS = [
    "GEMINI_API_KEY", "GEMINI_MODEL", "AGENTD_CONFIG", "OPENAI_API_KEY", "GOOGLE_API_KEY",
    *[f"AGENTD_MODEL_{r}{s}" for r in ("DEFAULT", "PLANNER", "WORKER", "VERIFIER", "EMBEDDINGS") for s in ("", "_FALLBACKS")],
]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No model settings from the developer's real .env leak into these tests."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ------------------------------------------------------------ config


def test_parse_spec_string_and_mapping():
    spec = parse_spec("openai:gpt-5.4-mini")
    assert (spec.provider, spec.model, spec.id) == ("openai", "gpt-5.4-mini", "openai:gpt-5.4-mini")

    spec = parse_spec({"model": "ollama:llama3.1", "base_url": "http://box:11434", "temperature": 0})
    assert spec.base_url == "http://box:11434" and spec.temperature == 0


def test_parse_spec_keeps_colons_in_model_name():
    assert parse_spec("openrouter:meta-llama/llama-4:free").model == "meta-llama/llama-4:free"


@pytest.mark.parametrize("bad", ["gpt-5", "openai:", ":gpt", 42, {"base_url": "x"}])
def test_parse_spec_rejects_bad_values(bad):
    with pytest.raises(ModelConfigError):
        parse_spec(bad)


def test_nothing_configured(clean_env):
    config = load_models_config()
    assert config.chain("planner") == []
    assert config.source == "none"


def test_legacy_gemini_key_still_works(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    config = load_models_config()
    assert [s.id for s in config.chain("planner")] == ["google_genai:gemini-3.5-flash"]
    assert config.chain("embeddings")[0].dimensions == 768


def test_yaml_roles_fallbacks_and_provider_defaults(clean_env):
    (clean_env / "agentd.yaml").write_text(
        """
models:
  default:
    model: openai:gpt-5.4-mini
    fallbacks: [google_genai:gemini-3.5-flash, ollama:llama3.1]
  planner: anthropic:claude-sonnet-5-5
providers:
  ollama: {base_url: "http://gpu-box:11434"}
""",
        encoding="utf-8",
    )
    config = load_models_config()

    assert [s.id for s in config.chain("planner")] == ["anthropic:claude-sonnet-5-5"]
    worker = config.chain("worker")  # falls back to default
    assert [s.id for s in worker] == ["openai:gpt-5.4-mini", "google_genai:gemini-3.5-flash", "ollama:llama3.1"]
    assert worker[2].base_url == "http://gpu-box:11434"
    assert config.chain("embeddings") == []  # embeddings never borrow a chat model


def test_env_overrides_yaml(clean_env, monkeypatch):
    (clean_env / "agentd.yaml").write_text("models:\n  default: openai:gpt-5.4-mini\n", encoding="utf-8")
    monkeypatch.setenv("AGENTD_MODEL_DEFAULT", "xai:grok-5")
    monkeypatch.setenv("AGENTD_MODEL_DEFAULT_FALLBACKS", "ollama:qwen3")
    assert [s.id for s in load_models_config().chain("worker")] == ["xai:grok-5", "ollama:qwen3"]


def test_unknown_role_in_yaml_is_an_error(clean_env):
    (clean_env / "agentd.yaml").write_text("models:\n  plannr: openai:gpt-5.4\n", encoding="utf-8")
    with pytest.raises(ModelConfigError, match="plannr"):
        load_models_config()


def test_api_key_lookup_uses_alternate_env(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem")
    assert parse_spec("google_genai:gemini-3.5-flash").api_key() == "gem"
    assert parse_spec("ollama:llama3.1").needs_key() is False
    custom = parse_spec({"model": "openai:local", "api_key_env": "LOCAL_KEY"})
    assert custom.key_envs() == ["LOCAL_KEY"]


# ------------------------------------------------------------ client


def config_with(*model_ids) -> ModelsConfig:
    specs = [parse_spec(m) for m in model_ids]
    return ModelsConfig(roles={"default": RoleConfig(specs[0], specs[1:])})


class Exploding:
    def __init__(self, message):
        self.message = message
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise RuntimeError(self.message)


def factory(models):
    return lambda spec: models[spec.id] if not isinstance(models[spec.id], Exception) else (_ for _ in ()).throw(models[spec.id])


def test_client_uses_first_model():
    client = LLMClient("planner", config_with("a:one", "b:two"),
                       factory({"a:one": FakeListChatModel(responses=['{"x": 1}']), "b:two": None}))
    assert client.generate_json("sys", "hi") == {"x": 1}
    assert client.last_model == "a:one"


def test_client_falls_back_when_key_runs_out():
    dead = Exploding("429 You exceeded your current quota")
    client = LLMClient("planner", config_with("a:one", "b:two"),
                       factory({"a:one": dead, "b:two": FakeListChatModel(responses=['{"ok": true}'])}))

    assert client.generate_json("sys", "hi") == {"ok": True}
    assert client.last_model == "b:two"
    assert dead.calls == 1


def test_client_falls_back_when_model_cannot_be_built():
    client = LLMClient("planner", config_with("a:one", "b:two"),
                       factory({"a:one": ModelConfigError("needs an API key"),
                                "b:two": FakeListChatModel(responses=["plain text"])}))
    assert client.invoke_text("sys", "hi") == "plain text"


def test_client_reports_every_failure():
    client = LLMClient("planner", config_with("a:one", "b:two"),
                       factory({"a:one": Exploding("401 bad key"), "b:two": Exploding("connection refused")}))
    with pytest.raises(AllModelsFailed) as info:
        client.invoke_text("sys", "hi")
    assert len(info.value.errors) == 2
    assert "401 bad key" in str(info.value) and "connection refused" in str(info.value)


def test_client_without_models_explains_how_to_fix():
    with pytest.raises(NoModelConfigured, match="AGENTD_MODEL_DEFAULT"):
        LLMClient("planner", ModelsConfig()).invoke_text("sys", "hi")


def test_validate_and_retry_reasks_on_bad_json():
    model = FakeListChatModel(responses=["not json at all", '{"answer": "fixed"}'])
    client = LLMClient("worker", config_with("a:one"), factory({"a:one": model}))

    result = client.validate_and_retry_json("sys", "hi", validator=lambda r: "answer" in r, max_retries=1)

    assert result == {"answer": "fixed"}


def test_validate_and_retry_does_not_loop_on_dead_chain():
    dead = Exploding("provider down")
    client = LLMClient("worker", config_with("a:one"), factory({"a:one": dead}))
    with pytest.raises(AllModelsFailed):
        client.validate_and_retry_json("sys", "hi", validator=lambda r: True, max_retries=3)
    assert dead.calls == 1


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here it is: {"a": 1} hope that helps'])
def test_parse_json_tolerates_wrapping(text):
    assert parse_json(text) == {"a": 1}


# ------------------------------------------------------------ planner wiring


# ------------------------------------------------------------ doctor


def test_doctor_flags_missing_key_and_unconfigured_roles(clean_env):
    from agentd.doctor import FAIL, OK, WARN, run_checks

    checks = {(c.role, c.model): c for c in run_checks(config_with("openai:gpt-5.4-mini", "ollama:llama3.1"))}

    assert checks[("planner", "openai:gpt-5.4-mini")].status == FAIL
    assert "OPENAI_API_KEY" in checks[("planner", "openai:gpt-5.4-mini")].message
    assert checks[("embeddings", "-")].status == WARN
    ollama = checks[("planner", "ollama:llama3.1")]
    assert ollama.status in (OK, FAIL)  # OK if langchain-ollama is installed
    if ollama.status == FAIL:
        assert "pip install langchain-ollama" in ollama.message


def test_cli_init_writes_config_once(clean_env, capsys):
    from agentd.cli import main

    assert main(["init"]) == 0
    assert (clean_env / "agentd.yaml").is_file()
    assert main(["init"]) == 1  # refuses to overwrite
    config = load_models_config()
    assert config.chain("worker")[0].id == "openai:gpt-5.4-mini"


def test_factory_caps_provider_retries_but_allows_override():
    from agentd.models.factory import DEFAULT_MAX_RETRIES, _kwargs

    assert _kwargs(parse_spec("openai:gpt-5.4-mini"))["max_retries"] == DEFAULT_MAX_RETRIES
    assert _kwargs(parse_spec({"model": "openai:gpt-5.4-mini", "max_retries": 5}))["max_retries"] == 5
    assert "max_retries" not in _kwargs(parse_spec("ollama:llama3.1"))
    assert "max_retries" not in _kwargs(parse_spec("openai:text-embedding-3-small"), embeddings=True)


# ------------------------------------------------------------ settings screen


def test_save_model_setting_writes_env_and_keeps_fallbacks(clean_env, monkeypatch):
    from agentd.models.settings import save_model_setting

    monkeypatch.setenv("GEMINI_API_KEY", "gem")  # an existing working setup
    env = clean_env / ".env"

    out = save_model_setting("openai", "gpt-5.4", "sk-new", env_path=env, ping=lambda spec: "pong")

    assert out["ok"] is True and out["fallbacks"] == ["google_genai:gemini-3.5-flash"]
    text = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-new" in text and "AGENTD_MODEL_DEFAULT=openai:gpt-5.4" in text
    assert [s.id for s in load_models_config().chain("planner")] == ["openai:gpt-5.4", "google_genai:gemini-3.5-flash"]
    assert "sk-new" not in str(out)  # the key is never echoed back


def test_save_model_setting_rejects_a_key_that_fails(clean_env, monkeypatch):
    import os
    from agentd.models.settings import save_model_setting

    env = clean_env / ".env"

    def expired(spec):
        raise RuntimeError("Error code: 401 - Your API key has expired.")

    out = save_model_setting("openai", "gpt-5.4", "sk-old", env_path=env, ping=expired)

    assert out["ok"] is False and "expired" in out["message"]
    assert not env.exists() or "sk-old" not in env.read_text(encoding="utf-8")
    assert os.getenv("OPENAI_API_KEY") is None
    assert load_models_config().chain("planner") == []


def test_save_model_setting_needs_a_key_for_cloud_providers(clean_env):
    from agentd.models.settings import save_model_setting

    with pytest.raises(ModelConfigError, match="needs an API key"):
        save_model_setting("openai", "gpt-5.4", "", env_path=clean_env / ".env", ping=lambda s: "pong")


def test_client_reloads_after_settings_change(clean_env, monkeypatch):
    from agentd.models import config as model_config

    client = LLMClient("planner")
    assert client.chain == []
    monkeypatch.setenv("AGENTD_MODEL_DEFAULT", "ollama:llama3.1")
    model_config.bump_config_version()
    assert [s.id for s in client.chain] == ["ollama:llama3.1"]


# ------------------------------------------------------------ model catalog


def _transport(handler):
    import httpx
    return httpx.MockTransport(handler)


def test_catalog_openai_filters_non_chat_and_sorts_newest_first(clean_env):
    import httpx
    from agentd.models.catalog import list_models

    def handler(request):
        assert request.url == "https://api.openai.com/v1/models"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [
            {"id": "gpt-4o", "created": 100}, {"id": "text-embedding-3-small", "created": 300},
            {"id": "gpt-5.4", "created": 200}, {"id": "whisper-1", "created": 50},
        ]})

    models = list_models("openai", "sk-test", transport=_transport(handler))
    assert [m["id"] for m in models] == ["gpt-5.4", "gpt-4o"]


def test_catalog_google_uses_header_key_and_only_chat_models(clean_env):
    import httpx
    from agentd.models.catalog import list_models

    def handler(request):
        assert "key" not in request.url.params and request.headers["x-goog-api-key"] == "g-key"
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-3.5-flash", "displayName": "Gemini 3.5 Flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        ]})

    models = list_models("google_genai", "g-key", transport=_transport(handler))
    assert models == [{"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash"}]


def test_catalog_anthropic_and_ollama_shapes(clean_env):
    import httpx
    from agentd.models.catalog import list_models

    def handler(request):
        if request.url.host == "api.anthropic.com":
            assert request.headers["x-api-key"] == "a-key"
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}]})
        return httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}]})

    assert list_models("anthropic", "a-key", transport=_transport(handler))[0]["id"] == "claude-sonnet-5-5"
    assert list_models("ollama", transport=_transport(handler))[0]["id"] == "llama3.1:latest"


def test_catalog_reports_rejected_key_plainly(clean_env):
    import httpx
    from agentd.models.catalog import ModelListError, list_models

    handler = lambda request: httpx.Response(401, json={"error": {"message": "Your API key has expired."}})
    with pytest.raises(ModelListError, match="rejected this API key.*expired"):
        list_models("openai", "sk-old", transport=_transport(handler))


def test_catalog_needs_a_key(clean_env):
    from agentd.models.catalog import list_models

    with pytest.raises(ModelConfigError, match="needs an API key"):
        list_models("openai", "")


def test_save_provider_key_stores_only_accepted_keys(clean_env):
    import os
    from agentd.models.catalog import ModelListError
    from agentd.models.settings import save_provider_key

    env = clean_env / ".env"

    def reject(provider, key):
        raise ModelListError("openai rejected this API key: expired")

    with pytest.raises(ModelListError):
        save_provider_key("openai", "sk-bad", env_path=env, lister=reject)
    assert not env.exists() or "sk-bad" not in env.read_text(encoding="utf-8")
    assert os.getenv("OPENAI_API_KEY") is None

    out = save_provider_key("openai", "sk-good", env_path=env, lister=lambda p, k: [{"id": "gpt-5.4", "label": "GPT-5.4"}])
    assert out["models"][0]["id"] == "gpt-5.4"
    assert "OPENAI_API_KEY=sk-good" in env.read_text(encoding="utf-8")
    assert "sk-good" not in str(out)


# ------------------------------------------------------------ effort level


def test_effort_is_passed_in_each_providers_own_words(clean_env, monkeypatch):
    from agentd.models.config import reset_effort, set_effort
    from agentd.models.factory import _kwargs

    assert "reasoning_effort" not in _kwargs(parse_spec("openai:gpt-5.4"))  # nothing chosen: model default
    token = set_effort("low")
    try:
        assert _kwargs(parse_spec("openai:gpt-5.4"))["reasoning_effort"] == "low"
        assert _kwargs(parse_spec("xai:grok-4.6"))["reasoning_effort"] == "low"
        assert _kwargs(parse_spec("anthropic:claude-sonnet-5-5"))["reasoning_effort"] == "low"
        assert _kwargs(parse_spec("google_genai:gemini-3.5-flash"))["thinking_level"] == "low"
        assert "reasoning_effort" not in _kwargs(parse_spec("ollama:llama3.1"))
        assert "reasoning_effort" not in _kwargs(parse_spec("openai:text-embedding-3-small"), embeddings=True)
        # a per-model setting in the config file wins
        assert _kwargs(parse_spec({"model": "openai:gpt-5.4", "reasoning_effort": "high"}))["reasoning_effort"] == "high"
    finally:
        reset_effort(token)
    monkeypatch.setenv("AGENTD_EFFORT", "high")
    assert _kwargs(parse_spec("xai:grok-4.6"))["reasoning_effort"] == "high"
    token = set_effort("nonsense")  # unknown values mean "model default"
    try:
        assert "reasoning_effort" not in _kwargs(parse_spec("xai:grok-4.6"))
    finally:
        reset_effort(token)


def test_a_model_that_refuses_effort_is_asked_again_without_it(clean_env):
    from agentd.models.config import current_effort, reset_effort, set_effort

    built = []

    class Picky:
        def __init__(self, effort):
            self.effort = effort

        def invoke(self, messages):
            if self.effort:
                raise RuntimeError("400 Unsupported parameter: 'reasoning_effort' is not supported with this model.")
            return "plain answer"

    def make(spec):
        built.append(current_effort())
        return Picky(current_effort())

    token = set_effort("high")
    try:
        client = LLMClient("worker", config_with("a:one"), make)
        assert client.invoke_text("sys", "hi") == "plain answer"
        assert built == ["high", None] and current_effort() == "high"
    finally:
        reset_effort(token)
