from pathlib import Path

from sqlalchemy import event, update
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
    return engine, async_sessionmaker(engine, expire_on_commit=False)
