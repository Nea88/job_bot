"""Single list of commands for both the Telegram "/" menu and /help, so they never drift apart."""

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger(__name__)

USER_COMMANDS = [
    ("filters", "Фильтры: создать, удалить, интервью, навыки"),
    ("interview", "Тестовое интервью по фильтру"),
    ("stats", "Топ навыков по вакансиям фильтра"),
    ("daily", "Вкл/выкл ежедневное интервью"),
    ("pause", "Приостановить рассылку"),
    ("resume", "Возобновить рассылку"),
    ("cancel", "Отменить создание фильтра"),
    ("help", "Список команд"),
    ("start", "Начать заново"),
]

ADMIN_COMMANDS = [
    ("channels", "Telegram-каналы: /channels add|remove t.me/name"),
    ("tglogin", "Войти в Telegram-аккаунт для чтения каналов"),
    ("run_now", "Собрать вакансии прямо сейчас"),
]


def help_text(is_admin: bool) -> str:
    """HTML for /help (the bot sends HTML, so descriptions like "<username>" must be escaped)."""
    lines = [f"/{name} — {escape(description)}" for name, description in USER_COMMANDS]
    if is_admin:
        lines += ["", "<b>Админ</b>"] + [f"/{name} — {escape(d)}" for name, d in ADMIN_COMMANDS]
    return "\n".join(lines)


def _menu(commands: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=name, description=description) for name, description in commands]


async def setup_commands(bot: Bot, admin_ids: list[int]) -> None:
    """Fill the "/" autocomplete menu; admins additionally see admin commands in their chat."""
    await bot.set_my_commands(_menu(USER_COMMANDS), scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(_menu(USER_COMMANDS + ADMIN_COMMANDS), scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramBadRequest as e:
            # the admin has not started the bot yet: Telegram does not know this chat
            log.warning("could not set admin commands for %s: %s", admin_id, e)
