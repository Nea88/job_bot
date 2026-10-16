from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.config import Settings
from job_bot.db.models import TgChannel
from job_bot.pipeline.collect import Collector
from job_bot.sources.telegram import is_invite_link, parse_channel

router = Router()

USAGE = "/channels add t.me/name … · /channels remove t.me/name …"


def _is_admin(message: Message, settings: Settings) -> bool:
    return message.from_user is not None and message.from_user.id in settings.admin_ids


@router.message(Command("channels"))
async def cmd_channels(message: Message, command: CommandObject, sm: async_sessionmaker, settings: Settings) -> None:
    """/channels [add|remove <link or @username> ...]"""
    if not _is_admin(message, settings):
        return
    parts = (command.args or "").split()
    action, items = (parts[0].lower(), parts[1:]) if parts else ("", [])
    report: list[str] = []
    async with sm() as session:
        if action in ("add", "remove") and items:
            for item in items:
                username = parse_channel(item)
                if username is None:
                    hint = "приватные инвайт-ссылки не поддерживаются" if is_invite_link(item) else "не похоже на канал"
                    report.append(f"⚠️ {escape(item)}: {hint}")
                    continue
                existing = await session.scalar(
                    select(TgChannel).where(func.lower(TgChannel.username) == username)
                )
                if action == "add":
                    if existing is None:
                        session.add(TgChannel(username=username))
                        report.append(f"➕ @{username}")
                    else:
                        report.append(f"уже есть: @{existing.username}")
                elif existing is not None:
                    await session.delete(existing)
                    report.append(f"➖ @{existing.username}")
                else:
                    report.append(f"нет в списке: @{username}")
            await session.commit()
        elif action:
            report.append(f"Не понял команду. {USAGE}")
        channels = list(await session.scalars(select(TgChannel).order_by(TgChannel.username)))

    listing = "\n".join(f"• @{c.username}" for c in channels) or "пусто"
    head = "\n".join(report) + "\n\n" if report else ""
    await message.answer(f"{head}Каналы:\n{listing}\n\n{escape(USAGE)}", disable_web_page_preview=True)


@router.message(Command("run_now"))
async def cmd_run_now(message: Message, collector: Collector, settings: Settings) -> None:
    if not _is_admin(message, settings):
        return
    await message.answer("Запускаю сбор…")
    count = await collector.run()
    await message.answer(f"Готово, новых вакансий: {count}.")
