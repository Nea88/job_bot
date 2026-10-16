from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.bot.commands import help_text, setup_commands
from job_bot.config import Settings
from job_bot.db.repo import get_or_create_user

router = Router()

INTRO = """\
Я раз в час собираю вакансии с hh.ru, Habr Career, Hirify и Telegram-каналов по твоим фильтрам \
и присылаю новые. Параллельно анализирую требования и могу собрать тестовое интервью \
по самым востребованным навыкам. Все команды — в меню по кнопке «/»."""


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, sm: async_sessionmaker, settings: Settings) -> None:
    async with sm() as session:
        user = await get_or_create_user(session, message.from_user.id)
        user.is_active = True
        await session.commit()
    is_admin = message.from_user.id in settings.admin_ids
    if is_admin:
        # setting admin commands at startup fails until the admin has opened the chat
        await setup_commands(bot, [message.from_user.id])
    await message.answer(f"Привет! {INTRO}\n\n{help_text(is_admin)}\n\nНачни с /filters → «Новый фильтр».")


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(f"{INTRO}\n\n{help_text(message.from_user.id in settings.admin_ids)}")


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
