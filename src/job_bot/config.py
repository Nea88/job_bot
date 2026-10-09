import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Home Assistant add-on options (written by Supervisor from the add-on config UI)
HA_OPTIONS_PATH = Path(os.environ.get("JOB_BOT_OPTIONS", "/data/options.json"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_ids: list[int] = []

    # "/data" inside the HA add-on, "data" locally; db and Telethon session live here
    data_dir: str | None = None
    db_url: str | None = None
    timezone: str = os.environ.get("TZ") or "Europe/Moscow"
    collect_interval_minutes: int = 60
    daily_interview_hour: int = 10

    hh_user_agent: str = "job-bot/0.1 (you@example.com)"
    hh_access_token: str | None = None

    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_session: str | None = None
    # StringSession from `scripts/telethon_login.py --string`; used where interactive login is impossible
    tg_session_string: str | None = None

    # empty: discover the Ollama add-on via the HA Supervisor API
    ollama_url: str | None = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_concurrency: int = 1
    ollama_timeout: float = 300
    ollama_num_ctx: int = 4096

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, value):
        # HA UI and .env produce "" for unset optional fields
        return None if value == "" else value

    @model_validator(mode="after")
    def _derive_paths(self) -> "Settings":
        if self.data_dir is None:
            self.data_dir = "/data" if HA_OPTIONS_PATH.exists() else "data"
        if self.db_url is None:
            self.db_url = f"sqlite+aiosqlite:///{self.data_dir}/job_bot.db"
        if self.tg_session is None:
            self.tg_session = f"{self.data_dir}/telethon"
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = [init_settings, env_settings]
        if HA_OPTIONS_PATH.exists():
            sources.append(JsonConfigSettingsSource(settings_cls, json_file=HA_OPTIONS_PATH))
        return (*sources, dotenv_settings, file_secret_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
