from pathlib import Path

from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from job_bot.db.models import Base, Filter, TgChannel
from job_bot.sources.telegram import parse_channel


async def init_db(db_url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    if db_url.startswith("sqlite"):
        path = db_url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(db_url, connect_args={"timeout": 30})

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    else:
        engine = create_async_engine(db_url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_columns)
        # before 0.1.2 the wizard stored "300" literally; it always meant thousands
        await conn.execute(update(Filter).where(Filter.salary_min < 1000).values(salary_min=Filter.salary_min * 1000))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _add_new_sources(sm)
    await _normalize_channels(sm)
    return engine, sm


# columns added after release: create_all does not alter existing tables
NEW_COLUMNS = {"vacancies": {"domain": "VARCHAR(100)", "tasks": "JSON"}}


def _add_columns(conn) -> None:
    for table, columns in NEW_COLUMNS.items():
        existing = {c["name"] for c in inspect(conn).get_columns(table)}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def _add_new_sources(sm: async_sessionmaker) -> None:
    """Filters that subscribed to every source before Hirify existed get Hirify too."""
    async with sm() as session:
        for f in await session.scalars(select(Filter)):
            if {"hh", "habr", "telegram"} <= set(f.sources) and "hirify" not in f.sources:
                f.sources = [*f.sources, "hirify"]
        await session.commit()


async def _normalize_channels(sm: async_sessionmaker) -> None:
    """Before 0.2.4 links like "t.me/name" were stored verbatim; keep only clean, unique usernames."""
    async with sm() as session:
        seen: set[str] = set()
        channels = list(await session.scalars(select(TgChannel).order_by(TgChannel.id)))
        # clean rows first, so a dirty duplicate is deleted rather than renamed onto a taken name
        channels.sort(key=lambda c: parse_channel(c.username) != c.username)
        for ch in channels:
            username = parse_channel(ch.username)
            if username is None or username in seen:
                await session.delete(ch)
                continue
            seen.add(username)
            if ch.username != username:
                ch.username = username
                ch.last_message_id = 0
        await session.commit()
