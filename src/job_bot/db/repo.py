from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_bot.db.models import Filter, User


async def get_or_create_user(session: AsyncSession, tg_id: int) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is None:
        user = User(tg_id=tg_id)
        session.add(user)
        await session.flush()
    return user


async def get_user(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def active_filters(session: AsyncSession) -> list[Filter]:
    rows = await session.scalars(select(Filter).join(User).where(User.is_active.is_(True)))
    return list(rows)


async def get_user_filter(session: AsyncSession, tg_id: int, filter_id: int) -> Filter | None:
    return await session.scalar(
        select(Filter).join(User).where(Filter.id == filter_id, User.tg_id == tg_id)
    )
