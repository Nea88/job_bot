"""One-time interactive login for the Telethon user account that reads vacancy channels.

    uv run python scripts/telethon_login.py            # saves a session file next to the db
    uv run python scripts/telethon_login.py --string   # prints a session string for the HA add-on option
"""

import argparse
import asyncio
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from job_bot.config import get_settings


async def main(as_string: bool) -> None:
    settings = get_settings()
    if not (settings.tg_api_id and settings.tg_api_hash):
        raise SystemExit("Set TG_API_ID and TG_API_HASH in .env (https://my.telegram.org)")
    if as_string:
        session = StringSession()
    else:
        Path(settings.tg_session).parent.mkdir(parents=True, exist_ok=True)
        session = settings.tg_session
    client = TelegramClient(session, settings.tg_api_id, settings.tg_api_hash)
    await client.start()  # prompts for phone, code and 2FA password
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (@{me.username})")
    if as_string:
        print("\nPaste this into the add-on option tg_session_string (keep it secret):\n")
        print(client.session.save())
    else:
        print(f"Session saved to {settings.tg_session}.session")
    await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--string", action="store_true", help="print a StringSession instead of saving a file")
    asyncio.run(main(parser.parse_args().string))
