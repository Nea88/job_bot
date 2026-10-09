import logging
import re

from telethon import TelegramClient
from telethon.tl.custom import Message

from job_bot.db.models import to_naive_utc
from job_bot.sources.base import RawVacancy

log = logging.getLogger(__name__)

FIRST_RUN_LIMIT = 50
MIN_LENGTH = 200
VACANCY_MARKERS = (
    "ваканс", "vacancy", "hiring", "ищем", "требовани", "зарплат", "з/п", "зп ",
    "salary", "обязанност", "#job", "#работа",
)
REMOTE_MARKERS = ("удален", "удалён", "remote")


def looks_like_vacancy(text: str) -> bool:
    lower = text.lower()
    return len(text) >= MIN_LENGTH and any(m in lower for m in VACANCY_MARKERS)


def parse_message(channel: str, msg_id: int, text: str, date) -> RawVacancy:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = re.sub(r"[*_#`]+", "", lines[0])[:200] if lines else "Вакансия"
    lower = text.lower()
    return RawVacancy(
        source="telegram",
        external_id=f"{channel}/{msg_id}",
        url=f"https://t.me/{channel}/{msg_id}",
        title=title,
        company=None,
        schedule="remote" if any(m in lower for m in REMOTE_MARKERS) else None,
        description=text,
        published_at=to_naive_utc(date) if date else None,
    )


class TelegramSource:
    """Reads new posts from vacancy channels via a Telethon user session."""

    name = "telegram"

    def __init__(self, client: TelegramClient):
        self._client = client

    async def fetch_channel(self, channel: str, last_message_id: int) -> tuple[list[RawVacancy], int]:
        """Returns new vacancy posts and the newest message id seen."""
        result: list[RawVacancy] = []
        newest = last_message_id
        kwargs = {"min_id": last_message_id} if last_message_id else {"limit": FIRST_RUN_LIMIT}
        msg: Message
        async for msg in self._client.iter_messages(channel, **kwargs):
            newest = max(newest, msg.id)
            text = msg.message or ""
            if looks_like_vacancy(text):
                result.append(parse_message(channel, msg.id, text, msg.date))
        return result, newest
