import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient
from telethon.sessions import StringSession

from job_bot.bot.handlers import build_router
from job_bot.bot.handlers.interview import send_interview
from job_bot.config import Settings, get_settings
from job_bot.db.models import User
from job_bot.db.session import init_db
from job_bot.interview.builder import InterviewService
from job_bot.llm.discovery import discover_ollama_url
from job_bot.llm.ollama import OllamaClient
from job_bot.pipeline.analyze import Analyzer
from job_bot.pipeline.collect import Collector
from job_bot.sources.habr import HabrSource
from job_bot.sources.hh import HHSource
from job_bot.sources.telegram import TelegramSource

log = logging.getLogger("job_bot")

MODEL_RETRY_DELAY = 60


async def daily_interviews(bot: Bot, sm: async_sessionmaker, interviews: InterviewService) -> None:
    async with sm() as session:
        users = list(
            await session.scalars(select(User).where(User.is_active.is_(True), User.daily_interview.is_(True)))
        )
    for user in users:
        for f in user.filters:
            try:
                await send_interview(bot, user.tg_id, interviews, f)
            except Exception:
                log.exception("daily interview failed for user %s filter %s", user.tg_id, f.id)


async def start_telegram(settings: Settings) -> TelegramClient | None:
    if not (settings.tg_api_id and settings.tg_api_hash):
        log.info("TG_API_ID/TG_API_HASH not set: Telegram channels source disabled")
        return None
    session = StringSession(settings.tg_session_string) if settings.tg_session_string else settings.tg_session
    client = TelegramClient(session, settings.tg_api_id, settings.tg_api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        log.warning("Telethon session is not authorized; run `python scripts/telethon_login.py [--string]` first")
        await client.disconnect()
        return None
    return client


async def resolve_ollama_url(settings: Settings) -> str:
    if settings.ollama_url:
        return settings.ollama_url
    url = await discover_ollama_url()
    if url is None:
        log.error("ollama_url is empty and no Ollama add-on was found; set ollama_url in the options")
        return "http://localhost:11434"
    log.info("found Ollama add-on at %s", url)
    return url


async def llm_worker(llm: OllamaClient, analyzer: Analyzer) -> None:
    """Make sure the model is available (pulls it on a fresh install), then process the analysis queue."""
    while True:
        try:
            await llm.ensure_model()
            break
        except Exception as e:
            log.warning("Ollama is not ready (%s), retrying in %ss", e, MODEL_RETRY_DELAY)
            await asyncio.sleep(MODEL_RETRY_DELAY)
    await analyzer.backfill()
    await analyzer.run()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()

    engine, sm = await init_db(settings.db_url)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    http = httpx.AsyncClient(timeout=30, follow_redirects=True)
    llm = OllamaClient(
        await resolve_ollama_url(settings),
        settings.ollama_model,
        settings.ollama_concurrency,
        settings.ollama_timeout,
        settings.ollama_num_ctx,
    )

    hh = HHSource(http, settings.hh_user_agent, settings.hh_access_token)
    tg_client = await start_telegram(settings)
    analyzer = Analyzer(sm, llm)
    collector = Collector(
        sm,
        bot,
        sources=[hh, HabrSource(http)],
        telegram=TelegramSource(tg_client) if tg_client else None,
        analyzer=analyzer,
    )
    interviews = InterviewService(sm, llm)

    dp = Dispatcher(sm=sm, settings=settings, hh=hh, collector=collector, interviews=interviews)
    dp.include_router(build_router())

    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        collector.run,
        "interval",
        minutes=settings.collect_interval_minutes,
        next_run_time=datetime.now(tz) + timedelta(seconds=15),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_interviews, "cron", hour=settings.daily_interview_hour, args=[bot, sm, interviews], max_instances=1
    )
    scheduler.start()

    analyzer_task = asyncio.create_task(llm_worker(llm, analyzer))

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        analyzer_task.cancel()
        if tg_client:
            await tg_client.disconnect()
        await llm.close()
        await http.aclose()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
