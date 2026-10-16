"""/tglogin: sign the Telethon user account in from the bot chat, so no session string is needed.

The session is saved to the file session in data_dir and picked up again on restart.
"""

import asyncio
import io
import logging
import re

import segno

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message
from telethon import TelegramClient, errors
from telethon.tl.types import auth

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
    qr = State()


QR_ATTEMPTS = 6  # each QR token lives ~30 s, so about 3 minutes in total
QR_HELP = (
    "Открой Telegram на телефоне с нужным аккаунтом → Настройки → Устройства → "
    "«Подключить устройство» и отсканируй код. Его нужно показать на другом экране "
    "(Telegram Desktop или web.telegram.org) или открыть ссылку на устройстве, где этот аккаунт уже открыт."
)


def normalize_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("8"):  # 8 900 ... -> +7 900 ...
        digits = "7" + digits[1:]
    return f"+{digits}" if 10 <= len(digits) <= 15 else None


def normalize_code(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    return digits if 4 <= len(digits) <= 8 else None


NEXT_TYPE = {
    auth.CodeTypeSms: "по SMS",
    auth.CodeTypeCall: "звонком",
    auth.CodeTypeFlashCall: "звонком-сбросом",
    auth.CodeTypeMissedCall: "пропущенным звонком",
    auth.CodeTypeFragmentSms: "SMS через Fragment",
}


def describe_sent(sent) -> tuple[str, bool]:
    """Where Telegram delivered the login code, and whether a code is expected at all."""
    kind = sent.type
    if isinstance(kind, auth.SentCodeTypeApp):
        where = (
            "в приложение Telegram — в служебный чат «Telegram» (с синей галочкой) на устройствах, "
            "где уже открыт этот аккаунт. Это не SMS."
        )
    elif isinstance(kind, (auth.SentCodeTypeSms, auth.SentCodeTypeSmsWord, auth.SentCodeTypeSmsPhrase,
                           auth.SentCodeTypeFirebaseSms)):
        where = "по SMS на этот номер."
    elif isinstance(kind, auth.SentCodeTypeFragmentSms):
        where = f"через Fragment: {kind.url}"
    elif isinstance(kind, auth.SentCodeTypeCall):
        where = "голосовым звонком: код продиктуют."
    elif isinstance(kind, auth.SentCodeTypeMissedCall):
        where = f"пропущенным звонком: код — последние {kind.length} цифр номера, с которого позвонят (+{kind.prefix}…)."
    elif isinstance(kind, auth.SentCodeTypeFlashCall):
        where = "звонком-сбросом: код — цифры номера, с которого позвонят."
    elif isinstance(kind, auth.SentCodeTypeEmailCode):
        where = f"на почту {kind.email_pattern}."
    elif isinstance(kind, auth.SentCodeTypeSetUpEmailRequired):
        return (
            "Telegram не отправил код: он требует сначала привязать почту для входа. В приложении Telegram на "
            "этом аккаунте: Настройки → Конфиденциальность → Почта для входа. Потом снова /tglogin.",
            False,
        )
    else:
        where = f"способом {type(kind).__name__}."
    text = "Код отправлен " + where + "\nКод так и не пришёл? Войди по QR-коду: /tgqr"
    if sent.next_type is not None:
        alt = NEXT_TYPE.get(type(sent.next_type), "другим способом")
        wait = f" через {sent.timeout} с" if sent.timeout else ""
        text += f"\nНе пришёл? Отправь /resend{wait} — Telegram пришлёт код {alt}."
    return text, True


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


@router.message(Command("resend"), TgLogin.code)
async def resend_code(message: Message, state: FSMContext) -> None:
    client = _pending.get(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer("Сессия входа потерялась (бот перезапускался?). Начни заново: /tglogin")
        return
    data = await state.get_data()
    try:
        # with a code hash cached for this phone Telethon sends auth.resendCode
        sent = await client.send_code_request(data["phone"])
    except Exception as e:
        await _drop_pending(message.from_user.id)
        await _report_send_error(message, state, e)
        return
    text, expect_code = describe_sent(sent)
    if not expect_code:
        await _drop_pending(message.from_user.id)
        await state.clear()
        await message.answer(text)
        return
    await state.update_data(phone_code_hash=sent.phone_code_hash)
    await message.answer(text + CODE_HINT)


def qr_png(url: str) -> bytes:
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=3)
    return buf.getvalue()


@router.message(Command("tgqr"))
async def cmd_tgqr(message: Message, bot: Bot, state: FSMContext, settings: Settings, collector: Collector) -> None:
    if not await _can_start_login(message, settings, collector):
        return
    await _drop_pending(message.from_user.id)
    client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
    await client.connect()
    try:
        qr = await client.qr_login()
    except Exception as e:
        await client.disconnect()
        log.exception("tglogin: qr_login failed")
        await message.answer(f"Telegram вернул ошибку: {type(e).__name__}: {e}")
        return
    _pending[message.from_user.id] = client
    await state.set_state(TgLogin.qr)
    task = asyncio.create_task(_qr_loop(message, bot, state, client, qr, collector))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


_tasks: set[asyncio.Task] = set()


async def _qr_loop(message: Message, bot: Bot, state: FSMContext, client: TelegramClient, qr, collector: Collector) -> None:
    """Show a fresh QR while waiting for a scan: tokens expire after ~30 seconds."""
    chat_id = message.chat.id
    shown = None
    try:
        for attempt in range(QR_ATTEMPTS):
            if attempt:
                await qr.recreate()
            caption = f"{QR_HELP}\n\nИли ссылка: <code>{qr.url}</code>\n\n/cancel — отменить"
            photo = BufferedInputFile(qr_png(qr.url), filename="login.png")
            if shown is not None:
                await bot.delete_message(chat_id, shown.message_id)
            shown = await bot.send_photo(chat_id, photo, caption=caption)
            try:
                await qr.wait()
            except asyncio.TimeoutError:
                if await state.get_state() != TgLogin.qr.state:  # cancelled meanwhile
                    return
                continue
            except errors.SessionPasswordNeededError:
                await state.set_state(TgLogin.password)
                await bot.send_message(chat_id, "QR принят. Включена двухэтапная проверка — отправь облачный пароль, сообщение удалю сразу.")
                return
            await _finish(message, state, client, collector)
            return
        await _drop_pending(message.from_user.id)
        await state.clear()
        await bot.send_message(chat_id, "QR-код так и не отсканировали. Попробуй ещё раз: /tgqr")
    except Exception as e:
        if await state.get_state() != TgLogin.qr.state:  # /cancel disconnected the client
            return
        log.exception("tglogin: QR login failed")
        await _drop_pending(message.from_user.id)
        await state.clear()
        await bot.send_message(chat_id, f"Вход по QR не удался: {type(e).__name__}: {e}")
    finally:
        if shown is not None:
            try:
                await bot.delete_message(chat_id, shown.message_id)
            except Exception:
                pass


async def _can_start_login(message: Message, settings: Settings, collector: Collector) -> bool:
    if message.from_user.id not in settings.admin_ids:
        return False
    if not (settings.tg_api_id and settings.tg_api_hash):
        await message.answer(
            "Сначала укажи tg_api_id и tg_api_hash в настройках аддона: их выдаёт https://my.telegram.org → "
            "API development tools. Потом перезапусти аддон и снова отправь /tglogin."
        )
        return False
    if collector.telegram is not None and await collector.telegram.client.is_user_authorized():
        me = await collector.telegram.client.get_me()
        await message.answer(f"Уже выполнен вход как @{me.username or me.first_name}. Каналы: /channels")
        return False
    return True


@router.message(Command("tglogin"))
async def cmd_tglogin(message: Message, state: FSMContext, settings: Settings, collector: Collector) -> None:
    if not await _can_start_login(message, settings, collector):
        return
    await _drop_pending(message.from_user.id)
    await state.set_state(TgLogin.phone)
    await message.answer(
        "Вход в Telegram-аккаунт, через который бот будет читать каналы (/cancel — отменить).\n\n"
        "Отправь номер телефона этого аккаунта, например +7 900 123-45-67.\n"
        "Или войди по QR-коду без кода из SMS: /tgqr"
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
    except Exception as e:
        await client.disconnect()
        await _report_send_error(message, state, e)
        return
    text, expect_code = describe_sent(sent)
    if not expect_code:
        await client.disconnect()
        await state.clear()
        await message.answer(text)
        return
    _pending[message.from_user.id] = client
    await state.update_data(phone=phone, phone_code_hash=sent.phone_code_hash)
    await state.set_state(TgLogin.code)
    await message.answer(text + CODE_HINT)


CODE_HINT = (
    "\n\n⚠️ Пришли код <b>с пробелами</b>, например <code>1 2 3 4 5</code>: "
    "если отправить его целиком, Telegram сочтёт код утёкшим и аннулирует."
)


async def _report_send_error(message: Message, state: FSMContext, e: Exception) -> None:
    if isinstance(e, errors.PhoneNumberInvalidError):
        await message.answer("Telegram не принял этот номер. Проверь и отправь ещё раз.")
        return  # stay on the phone step
    await state.clear()
    if isinstance(e, errors.FloodWaitError):
        await message.answer(f"Telegram просит подождать {e.seconds} с перед новой попыткой входа.")
    elif isinstance(e, errors.ApiIdInvalidError):
        await message.answer("Telegram отклонил tg_api_id/tg_api_hash. Проверь их на my.telegram.org.")
    elif isinstance(e, errors.PhoneNumberBannedError):
        await message.answer("Этот номер заблокирован в Telegram.")
    else:
        log.exception("tglogin: send_code_request failed")
        await message.answer(f"Telegram вернул ошибку: {type(e).__name__}: {e}. Подробности в логе аддона.")


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
        await message.answer("Не вижу код. Пришли цифры с пробелами: 1 2 3 4 5 (или /resend)")
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
