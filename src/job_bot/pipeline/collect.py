import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.bot.formatting import format_digest, format_vacancy
from job_bot.bot.keyboards import vacancy_kb
from job_bot.db.models import Filter, Sent, SourceState, TgChannel, User, Vacancy, utcnow
from job_bot.db.repo import active_filters
from job_bot.matching import contains, matches, query_for
from job_bot.pipeline.analyze import Analyzer
from job_bot.sources.base import RawVacancy, Source
from job_bot.sources.hirify import HirifySource
from job_bot.sources.telegram import TelegramSource

log = logging.getLogger(__name__)

FIRST_RUN_WINDOW = timedelta(days=1)
OVERLAP = timedelta(minutes=10)
MAX_CARDS_PER_RUN = 10
SEND_DELAY = 0.05  # stay well under Telegram's ~30 msg/s


def _worth_fetching(raw: RawVacancy, filters: list[Filter], source: str) -> bool:
    """Cheap pre-check on listing data for sources that return every profession: skip the detail
    request unless some filter's keyword is already in the title or tags."""
    text = f"{raw.title}\n{raw.description}".lower()
    return any(
        source in f.sources and any(contains(text, k) for k in [*f.keywords_any, *f.keywords_all])
        for f in filters
    )


class Collector:
    def __init__(
        self,
        sm: async_sessionmaker,
        bot: Bot,
        sources: list[Source],
        telegram: TelegramSource | None,
        analyzer: Analyzer,
        hirify: HirifySource | None = None,
    ):
        self._sm = sm
        self._bot = bot
        self._sources = sources
        self._telegram = telegram
        self._hirify = hirify
        self._analyzer = analyzer
        self._lock = asyncio.Lock()

    async def run(self) -> int:
        """Fetch → dedup → store → notify → queue for analysis. Returns the number of new vacancies."""
        if self._lock.locked():
            log.info("collect already running, skipping")
            return 0
        async with self._lock:
            async with self._sm() as session:
                filters = await active_filters(session)

            new_ids: list[int] = []
            for source in self._sources:
                queries = {query_for(f) for f in filters if source.name in f.sources}
                for query in queries:
                    try:
                        new_ids += await self._collect_query(source, query)
                    except Exception:
                        # one broken source/query must not stop the rest
                        log.exception("%s: query failed", source.name)
            if self._hirify and any("hirify" in f.sources for f in filters):
                try:
                    raws = [r for r in await self._hirify.fetch_latest() if _worth_fetching(r, filters, "hirify")]
                    new_ids += await self._store(raws, self._hirify.enrich)
                except Exception:
                    log.exception("hirify: collection failed")
            if self._telegram and any("telegram" in f.sources for f in filters):
                new_ids += await self._collect_telegram()

            log.info("collected %d new vacancies", len(new_ids))
            matched = await self._notify(new_ids)
            # the LLM is slow (about a minute per vacancy on a Pi): analyze only what someone subscribed to
            self._analyzer.enqueue(matched)
            await self._analyzer.backfill()
            return len(new_ids)

    async def _collect_query(self, source: Source, query) -> list[int]:
        started = utcnow()
        async with self._sm() as session:
            state = await session.scalar(
                select(SourceState).where(SourceState.source == source.name, SourceState.query_key == query.key)
            )
            since = state.last_run_at - OVERLAP if state else started - FIRST_RUN_WINDOW

        raws = await source.search(query, since)
        ids = await self._store(raws, source.enrich)

        async with self._sm() as session:
            state = await session.scalar(
                select(SourceState).where(SourceState.source == source.name, SourceState.query_key == query.key)
            )
            if state is None:
                session.add(SourceState(source=source.name, query_key=query.key, last_run_at=started))
            else:
                state.last_run_at = started
            await session.commit()
        return ids

    async def _collect_telegram(self) -> list[int]:
        assert self._telegram is not None
        async with self._sm() as session:
            channels = list(await session.scalars(select(TgChannel)))
        ids: list[int] = []
        for ch in channels:
            try:
                raws, newest = await self._telegram.fetch_channel(ch.username, ch.last_message_id)
            except Exception:
                log.exception("telegram: failed to read @%s", ch.username)
                continue
            ids += await self._store(raws)
            async with self._sm() as session:
                row = await session.get(TgChannel, ch.id)
                if row is not None:
                    row.last_message_id = newest
                    await session.commit()
        return ids

    async def _store(
        self, raws: list[RawVacancy], enrich: Callable[[RawVacancy], Awaitable[RawVacancy]] | None = None
    ) -> list[int]:
        ids: list[int] = []
        seen: set[tuple[str, str]] = set()
        for raw in raws:
            if (raw.source, raw.external_id) in seen:
                continue
            seen.add((raw.source, raw.external_id))
            async with self._sm() as session:
                exists = await session.scalar(
                    select(Vacancy.id)
                    .where(
                        or_(
                            and_(Vacancy.source == raw.source, Vacancy.external_id == raw.external_id),
                            Vacancy.content_hash == raw.content_hash,
                        )
                    )
                    .limit(1)
                )
                if exists:
                    continue
            # enrich outside of a DB transaction: it does network calls
            if enrich is not None:
                raw = await enrich(raw)
            async with self._sm() as session:
                vacancy = Vacancy(**raw.as_model_kwargs())
                session.add(vacancy)
                await session.commit()
                ids.append(vacancy.id)
        return ids

    async def _notify(self, vacancy_ids: list[int]) -> list[int]:
        """Send matches to users; returns ids of vacancies that matched at least one filter."""
        if not vacancy_ids:
            return []
        async with self._sm() as session:
            vacancies = list(
                await session.scalars(
                    select(Vacancy).where(Vacancy.id.in_(vacancy_ids)).order_by(Vacancy.published_at.desc())
                )
            )
            filters = await active_filters(session)
            already = set(
                (await session.execute(select(Sent.user_id, Sent.vacancy_id).where(Sent.vacancy_id.in_(vacancy_ids)))).tuples()
            )

        # one message per (user, vacancy), even if several of the user's filters match
        per_user: dict[int, list[tuple[Filter, Vacancy]]] = defaultdict(list)
        for v in vacancies:
            taken: set[int] = set()
            for f in filters:
                if f.user_id in taken or (f.user_id, v.id) in already:
                    continue
                if matches(f, v):
                    per_user[f.user_id].append((f, v))
                    taken.add(f.user_id)

        for user_id, items in per_user.items():
            await self._send_to_user(user_id, items[0][0].user.tg_id, items)
        return sorted({v.id for items in per_user.values() for _, v in items})

    async def _send_to_user(self, user_id: int, tg_id: int, items: list[tuple[Filter, Vacancy]]) -> None:
        cards, rest = items[:MAX_CARDS_PER_RUN], items[MAX_CARDS_PER_RUN:]
        try:
            for f, v in cards:
                await self._send(
                    tg_id, format_vacancy(v, f.name), reply_markup=vacancy_kb(v, f.id), disable_web_page_preview=True
                )
            if rest:
                await self._send(tg_id, format_digest([v for _, v in rest]), disable_web_page_preview=True)
        except TelegramForbiddenError:
            log.info("user %s blocked the bot, deactivating", tg_id)
            async with self._sm() as session:
                user = await session.get(User, user_id)
                if user is not None:
                    user.is_active = False
                    await session.commit()
            return

        async with self._sm() as session:
            session.add_all(Sent(user_id=user_id, filter_id=f.id, vacancy_id=v.id) for f, v in items)
            await session.commit()

    async def _send(self, chat_id: int, text: str, **kwargs) -> None:
        try:
            await self._bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await self._bot.send_message(chat_id, text, **kwargs)
        await asyncio.sleep(SEND_DELAY)
