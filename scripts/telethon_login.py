"""One-time interactive login that creates the Telethon session file used to read channels.

    uv run python scripts/telethon_login.py
"""

import asyncio
from pathlib import Path

from telethon import TelegramClient

from job_bot.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not (settings.tg_api_id and settings.tg_api_hash):
        raise SystemExit("Set TG_API_ID and TG_API_HASH in .env (https://my.telegram.org)")
    Path(settings.tg_session).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
    await client.start()  # prompts for phone, code and 2FA password
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (@{me.username}); session saved to {settings.tg_session}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
