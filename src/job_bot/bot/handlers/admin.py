from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.config import Settings
from job_bot.db.models import TgChannel
from job_bot.pipeline.collect import Collector

router = Router()


def _is_admin(message: Message, settings: Settings) -> bool:
    return message.from_user is not None and message.from_user.id in settings.admin_ids


@router.message(Command("channels"))
async def cmd_channels(message: Message, command: CommandObject, sm: async_sessionmaker, settings: Settings) -> None:
    """/channels [add|remove <username>]"""
    if not _is_admin(message, settings):
        return
    args = (command.args or "").split()
    async with sm() as session:
        if len(args) == 2 and args[0] in ("add", "remove"):
            username = args[1].removeprefix("https://t.me/").lstrip("@").strip("/")
            existing = await session.scalar(select(TgChannel).where(TgChannel.username == username))
            if args[0] == "add" and existing is None:
                session.add(TgChannel(username=username))
            elif args[0] == "remove" and existing is not None:
                await session.delete(existing)
            await session.commit()
        channels = list(await session.scalars(select(TgChannel).order_by(TgChannel.username)))
    listing = "\n".join(f"• @{c.username}" for c in channels) or "пусто"
    await message.answer(f"Каналы:\n{listing}\n\n/channels add &lt;username&gt; · /channels remove &lt;username&gt;")


@router.message(Command("run_now"))
async def cmd_run_now(message: Message, collector: Collector, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    await message.answer("Запускаю сбор…")
    count = await collector.run()
    await message.answer(f"Готово, новых вакансий: {count}.")
