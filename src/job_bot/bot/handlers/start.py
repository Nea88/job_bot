from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.db.repo import get_or_create_user

router = Router()

HELP = """\
Я раз в час собираю вакансии с hh.ru, Habr Career, Hirify и Telegram-каналов по твоим фильтрам \
и присылаю новые. Параллельно анализирую требования и могу собрать тестовое интервью \
по самым востребованным навыкам.

/filters — фильтры (создать, удалить, интервью, навыки)
/interview — тестовое интервью по фильтру
/stats — топ навыков по фильтру
/daily — вкл/выкл ежедневное интервью
/pause, /resume — приостановить/возобновить рассылку"""


@router.message(CommandStart())
async def cmd_start(message: Message, sm: async_sessionmaker) -> None:
    async with sm() as session:
        user = await get_or_create_user(session, message.from_user.id)
        user.is_active = True
        await session.commit()
    await message.answer("Привет! " + HELP + "\n\nНачни с /filters → «Новый фильтр».")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("pause", "resume"))
async def cmd_pause(message: Message, sm: async_sessionmaker) -> None:
    active = message.text.startswith("/resume")
    async with sm() as session:
        user = await get_or_create_user(session, message.from_user.id)
        user.is_active = active
        await session.commit()
    await message.answer("Рассылка включена." if active else "Рассылка на паузе. /resume — включить.")


@router.message(Command("daily"))
async def cmd_daily(message: Message, sm: async_sessionmaker) -> None:
    async with sm() as session:
        user = await get_or_create_user(session, message.from_user.id)
        user.daily_interview = not user.daily_interview
        enabled = user.daily_interview
        await session.commit()
    await message.answer(
        "Ежедневное интервью включено: буду присылать его утром по каждому фильтру."
        if enabled
        else "Ежедневное интервью выключено."
    )
