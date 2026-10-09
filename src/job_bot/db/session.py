from pathlib import Path

from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from job_bot.db.models import Base, Filter


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
        # before 0.1.2 the wizard stored "300" literally; it always meant thousands
        await conn.execute(update(Filter).where(Filter.salary_min < 1000).values(salary_min=Filter.salary_min * 1000))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    await _add_new_sources(sm)
    return engine, sm


async def _add_new_sources(sm: async_sessionmaker) -> None:
    """Filters that subscribed to every source before Hirify existed get Hirify too."""
    async with sm() as session:
        for f in await session.scalars(select(Filter)):
            if {"hh", "habr", "telegram"} <= set(f.sources) and "hirify" not in f.sources:
                f.sources = [*f.sources, "hirify"]
        await session.commit()
