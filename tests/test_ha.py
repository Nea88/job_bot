import json

import httpx
import pytest

from job_bot import config
from job_bot.llm.discovery import pick_ollama_url
from job_bot.llm.ollama import LLMError, OllamaClient


@pytest.fixture
def ha_options(tmp_path, monkeypatch):
    for var in ("BOT_TOKEN", "ADMIN_IDS", "OLLAMA_URL", "DB_URL", "DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    path = tmp_path / "options.json"
    monkeypatch.setattr(config, "HA_OPTIONS_PATH", path)
    return path


def test_settings_from_ha_options(ha_options):
    ha_options.write_text(json.dumps({
        "bot_token": "123:abc", "admin_ids": [1, 2], "ollama_url": "", "ollama_model": "qwen2.5:3b",
        "hh_access_token": "", "collect_interval_minutes": 30,
    }))
    s = config.Settings()
    assert s.bot_token == "123:abc" and s.admin_ids == [1, 2]
    assert s.ollama_url is None and s.hh_access_token is None
    assert s.collect_interval_minutes == 30
    assert s.data_dir == "/data"
    assert s.db_url == "sqlite+aiosqlite:////data/job_bot.db"
    assert s.tg_session == "/data/telethon"


def test_env_overrides_options(ha_options, monkeypatch):
    ha_options.write_text(json.dumps({"bot_token": "from-options"}))
    monkeypatch.setenv("BOT_TOKEN", "from-env")
    assert config.Settings().bot_token == "from-env"


def test_local_defaults_without_options(ha_options, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "x")
    s = config.Settings()
    assert s.data_dir == "data" and s.db_url == "sqlite+aiosqlite:///data/job_bot.db"


def test_pick_ollama_url_prefers_started():
    addons = [
        {"slug": "core_samba", "state": "started"},
        {"slug": "abc123_ollama", "state": "stopped"},
        {"slug": "local_ollama", "state": "started"},
    ]
    assert pick_ollama_url(addons) == "http://local-ollama:11434"
    assert pick_ollama_url([{"slug": "core_samba"}]) is None


def _client(handler) -> OllamaClient:
    client = OllamaClient("http://ollama:11434", "qwen2.5:3b")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_ensure_model_skips_pull_when_present():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})

    await _client(handler).ensure_model()
    assert calls == ["/api/tags"]


async def test_ensure_model_pulls_missing():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        assert json.loads(request.content)["model"] == "qwen2.5:3b"
        return httpx.Response(200, json={"status": "success"})

    await _client(handler).ensure_model()


async def test_ensure_model_pull_error():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"status": "error: manifest not found"})

    with pytest.raises(LLMError):
        await _client(handler).ensure_model()
