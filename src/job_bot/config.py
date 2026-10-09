from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_ids: list[int] = []

    db_url: str = "sqlite+aiosqlite:///data/job_bot.db"
    timezone: str = "Europe/Moscow"
    collect_interval_minutes: int = 60
    daily_interview_hour: int = 10

    hh_user_agent: str = "job-bot/0.1 (you@example.com)"
    hh_access_token: str | None = None

    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_session: str = "data/telethon"

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_concurrency: int = 1
    ollama_timeout: float = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
