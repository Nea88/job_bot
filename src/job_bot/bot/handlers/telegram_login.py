"""/tglogin: sign the Telethon user account in from the bot chat, so no session string is needed.

The session is saved to the file session in data_dir and picked up again on restart.
"""

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from telethon import TelegramClient, errors

from job_bot.config import Settings
from job_bot.pipeline.collect import Collector
from job_bot.sources.telegram import TelegramSource

log = logging.getLogger(__name__)
router = Router()

# Telethon clients waiting for a code/password, by admin id (not serializable into FSM storage)
_pending: dict[int, TelegramClient] = {}


class TgLogin(StatesGroup):
    phone = State()
    code = State()
    password = State()


def normalize_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("8"):  # 8 900 ... -> +7 900 ...
        digits = "7" + digits[1:]
    return f"+{digits}" if 10 <= len(digits) <= 15 else None


def normalize_code(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    return digits if 5 <= len(digits) <= 6 else None


async def _forget(message: Message) -> None:
    """Delete messages with codes/passwords from the chat history (best effort)."""
    try:
        await message.delete()
    except Exception:
        log.debug("could not delete message %s", message.message_id)


async def _drop_pending(user_id: int) -> None:
    client = _pending.pop(user_id, None)
    if client is not None:
        await client.disconnect()


async def _finish(message: Message, state: FSMContext, client: TelegramClient, collector: Collector) -> None:
    _pending.pop(message.from_user.id, None)
    await state.clear()
    me = await client.get_me()
    old = collector.telegram
    collector.telegram = TelegramSource(client)
    if old is not None:
        await old.client.disconnect()
    await message.answer(
        f"Готово, вошёл как {me.first_name} (@{me.username or '—'}). Каналы будут читаться со следующего сбора.\n"
        "Отозвать доступ: Telegram → Настройки → Устройства."
    )


# registered before the step handlers, otherwise "/cancel" would be taken as a phone or code
@router.message(Command("cancel"), StateFilter(TgLogin))
async def cancel_login(message: Message, state: FSMContext) -> None:
    await _drop_pending(message.from_user.id)
    await state.clear()
    await message.answer("Вход отменён.")


@router.message(Command("tglogin"))
async def cmd_tglogin(message: Message, state: FSMContext, settings: Settings, collector: Collector) -> None:
    if message.from_user.id not in settings.admin_ids:
        return
    if not (settings.tg_api_id and settings.tg_api_hash):
        await message.answer(
            "Сначала укажи tg_api_id и tg_api_hash в настройках аддона: их выдаёт https://my.telegram.org → "
            "API development tools. Потом перезапусти аддон и снова отправь /tglogin."
        )
        return
    if collector.telegram is not None and await collector.telegram.client.is_user_authorized():
        me = await collector.telegram.client.get_me()
        await message.answer(f"Уже выполнен вход как @{me.username or me.first_name}. Каналы: /channels")
        return
    await _drop_pending(message.from_user.id)
    await state.set_state(TgLogin.phone)
    await message.answer(
        "Вход в Telegram-аккаунт, через который бот будет читать каналы (/cancel — отменить).\n\n"
        "Отправь номер телефона этого аккаунта, например +7 900 123-45-67."
    )


@router.message(TgLogin.phone, F.text)
async def step_phone(message: Message, state: FSMContext, settings: Settings) -> None:
    phone = normalize_phone(message.text)
    if phone is None:
        await message.answer("Не похоже на номер. Пример: +7 900 123-45-67")
        return
    client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        await client.disconnect()
        await message.answer("Telegram не принял этот номер. Проверь и отправь ещё раз.")
        return
    except errors.FloodWaitError as e:
        await client.disconnect()
        await state.clear()
        await message.answer(f"Telegram просит подождать {e.seconds} с перед новой попыткой входа.")
        return
    _pending[message.from_user.id] = client
    await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash)
    await state.set_state(TgLogin.code)
    await message.answer(
        "Код отправлен в Telegram (в чат «Telegram» на этом аккаунте).\n\n"
        "⚠️ Пришли его <b>с пробелами</b>, например <code>1 2 3 4 5</code>: "
        "если отправить код целиком, Telegram сочтёт его утёкшим и аннулирует."
    )


@router.message(TgLogin.code, F.text)
async def step_code(message: Message, state: FSMContext, collector: Collector) -> None:
    await _forget(message)
    client = _pending.get(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer("Сессия входа потерялась (бот перезапускался?). Начни заново: /tglogin")
        return
    code = normalize_code(message.text)
    if code is None:
        await message.answer("Код — это 5 цифр. Пришли с пробелами: 1 2 3 4 5")
        return
    data = await state.get_data()
    try:
        await client.sign_in(data["phone"], code, phone_code_hash=data["phone_code_hash"])
    except errors.SessionPasswordNeededError:
        await state.set_state(TgLogin.password)
        await message.answer("Включена двухэтапная проверка. Отправь облачный пароль — сообщение удалю сразу.")
        return
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
        await message.answer("Неверный код, попробуй ещё раз (с пробелами).")
        return
    except errors.PhoneCodeExpiredError:
        await _drop_pending(message.from_user.id)
        await state.clear()
        await message.answer("Код истёк — возможно, его отправили без пробелов. Начни заново: /tglogin")
        return
    await _finish(message, state, client, collector)


@router.message(TgLogin.password, F.text)
async def step_password(message: Message, state: FSMContext, collector: Collector) -> None:
    password = message.text
    await _forget(message)
    client = _pending.get(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer("Сессия входа потерялась (бот перезапускался?). Начни заново: /tglogin")
        return
    try:
        await client.sign_in(password=password)
    except errors.PasswordHashInvalidError:
        await message.answer("Неверный пароль, попробуй ещё раз.")
        return
    await _finish(message, state, client, collector)
