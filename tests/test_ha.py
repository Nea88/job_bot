import json

import httpx
import pytest

from job_bot import config
from job_bot.llm import discovery
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


def test_ollama_slug_from_own_slug():
    assert discovery.ollama_slug_for("fbdab211_job_bot") == "fbdab211_ollama"
    assert discovery.ollama_slug_for("local_job_bot") == "local_ollama"
    assert discovery.url_for_slug("fbdab211_ollama") == "http://fbdab211-ollama:11434"


@pytest.fixture
def supervisor(monkeypatch):
    """Route discovery's Supervisor calls to a handler dict {path: (status, json)}."""
    routes: dict[str, tuple[int, dict]] = {}
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    real_client = httpx.AsyncClient

    def handler(request):
        assert request.headers["Authorization"] == "Bearer t"
        status, body = routes.get(request.url.path, (403, {"result": "error"}))
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        discovery.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return routes


async def test_discover_ollama(supervisor):
    supervisor["/addons/self/info"] = (200, {"data": {"slug": "fbdab211_job_bot"}})
    supervisor["/addons/fbdab211_ollama/info"] = (200, {"data": {"state": "started"}})
    assert await discovery.discover_ollama_url() == "http://fbdab211-ollama:11434"


async def test_discover_ollama_not_installed(supervisor):
    supervisor["/addons/self/info"] = (200, {"data": {"slug": "fbdab211_job_bot"}})
    assert await discovery.discover_ollama_url() is None


async def test_discover_without_supervisor(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert await discovery.discover_ollama_url() is None


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
