import pytest

from job_bot.sources.telegram import parse_channel


@pytest.mark.parametrize(
    "text",
    [
        "t.me/revacancy_global",
        "https://t.me/revacancy_global",
        "http://t.me/revacancy_global/",
        "https://www.t.me/revacancy_global",
        "telegram.me/revacancy_global",
        "https://t.me/s/revacancy_global",
        "https://t.me/revacancy_global/1234",
        "t.me/revacancy_global?single",
        "@revacancy_global",
        "revacancy_global",
        "@ReVacancy_Global",
    ],
)
def test_parse_channel_forms(text):
    assert parse_channel(text) == "revacancy_global"


@pytest.mark.parametrize(
    "text", ["t.me/+AbCdEf123", "https://t.me/joinchat/AbCdEf", "t.me/", "1abc", "ab", "https://example.com/x"]
)
def test_parse_channel_rejects(text):
    assert parse_channel(text) is None


async def test_stored_channels_are_normalized(tmp_path):
    from sqlalchemy import select

    from job_bot.db.models import TgChannel
    from job_bot.db.session import init_db

    url = f"sqlite+aiosqlite:///{tmp_path}/db.sqlite"
    engine, sm = await init_db(url)
    async with sm() as session:
        session.add_all([
            TgChannel(username="t.me/revacancy_global", last_message_id=5),
            TgChannel(username="https://t.me/Other_Jobs"),
            TgChannel(username="other_jobs", last_message_id=7),
            TgChannel(username="t.me/+invite"),
        ])
        await session.commit()
    await engine.dispose()

    engine, sm = await init_db(url)
    async with sm() as session:
        rows = {c.username: c.last_message_id for c in await session.scalars(select(TgChannel))}
    await engine.dispose()
    assert rows == {"revacancy_global": 0, "other_jobs": 7}
