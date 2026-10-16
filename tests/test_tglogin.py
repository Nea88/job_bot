from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from telethon import errors
from telethon.tl.types import auth

from job_bot.bot.handlers import telegram_login as tl


class FakeClient:
    instances: list["FakeClient"] = []

    sent_type = auth.SentCodeTypeApp(length=5)

    def __init__(self, *args, password_required=True):
        self.sends = 0
        self.signed_in = False
        self.password_required = password_required
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def send_code_request(self, phone):
        self.phone = phone
        self.sends += 1
        return auth.SentCode(
            type=self.sent_type if self.sends == 1 else auth.SentCodeTypeSms(length=5),
            phone_code_hash="hash",
            next_type=auth.CodeTypeSms(),
            timeout=60,
        )

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if password is not None:
            if password != "secret":
                raise errors.PasswordHashInvalidError(request=None)
            self.signed_in = True
            return
        assert phone_code_hash == "hash"
        if code != "12345":
            raise errors.PhoneCodeInvalidError(request=None)
        if self.password_required:
            raise errors.SessionPasswordNeededError(request=None)
        self.signed_in = True

    async def is_user_authorized(self):
        return self.signed_in

    async def get_me(self):
        return SimpleNamespace(first_name="Dima", username="dima")


class Msg:
    def __init__(self, text):
        self.text = text
        self.from_user = SimpleNamespace(id=1)
        self.message_id = 1
        self.deleted = False
        self.replies: list[str] = []

    async def answer(self, text, **kw):
        self.replies.append(text)

    async def delete(self):
        self.deleted = True


@pytest.fixture
def env(monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(tl, "TelegramClient", FakeClient)
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=0, chat_id=1, user_id=1))
    settings = SimpleNamespace(admin_ids=[1], tg_api_id=1, tg_api_hash="h", tg_session="/tmp/x")
    collector = SimpleNamespace(telegram=None)
    return state, settings, collector


def test_normalizers():
    assert tl.normalize_phone("8 (900) 123-45-67") == "+79001234567"
    assert tl.normalize_phone("+7 900 123 45 67") == "+79001234567"
    assert tl.normalize_phone("12") is None
    assert tl.normalize_code("1 2 3 4 5") == "12345"
    assert tl.normalize_code("12-345") == "12345"
    assert tl.normalize_code("12") is None
    assert tl.normalize_code("1 2 3 4 5 6") == "123456"


async def test_full_login_with_2fa(env):
    state, settings, collector = env
    await tl.cmd_tglogin(Msg("/tglogin"), state, settings, collector)
    assert await state.get_state() == tl.TgLogin.phone.state

    await tl.step_phone(Msg("+7 900 123-45-67"), state, settings)
    client = FakeClient.instances[-1]
    assert client.phone == "+79001234567"
    assert await state.get_state() == tl.TgLogin.code.state

    wrong = Msg("5 4 3 2 1")
    await tl.step_code(wrong, state, collector)
    assert wrong.deleted and "Неверный код" in wrong.replies[0]

    code = Msg("1 2 3 4 5")
    await tl.step_code(code, state, collector)
    assert code.deleted and await state.get_state() == tl.TgLogin.password.state

    password = Msg("secret")
    await tl.step_password(password, state, collector)
    assert password.deleted
    assert await state.get_state() is None
    assert collector.telegram is not None and collector.telegram.client is client
    assert "Готово" in password.replies[-1]
    assert 1 not in tl._pending


async def test_cancel_disconnects_pending_client(env):
    state, settings, collector = env
    await tl.cmd_tglogin(Msg("/tglogin"), state, settings, collector)
    await tl.step_phone(Msg("+79001234567"), state, settings)
    await tl.cancel_login(Msg("/cancel"), state)
    assert FakeClient.instances[-1].disconnected
    assert await state.get_state() is None and 1 not in tl._pending


async def test_requires_api_credentials(env):
    state, settings, collector = env
    settings.tg_api_id = None
    msg = Msg("/tglogin")
    await tl.cmd_tglogin(msg, state, settings, collector)
    assert "my.telegram.org" in msg.replies[0] and await state.get_state() is None


async def test_non_admin_ignored(env):
    state, settings, collector = env
    settings.admin_ids = [2]
    msg = Msg("/tglogin")
    await tl.cmd_tglogin(msg, state, settings, collector)
    assert msg.replies == [] and await state.get_state() is None


async def test_code_destination_and_resend(env):
    state, settings, collector = env
    await tl.cmd_tglogin(Msg("/tglogin"), state, settings, collector)
    phone = Msg("+79001234567")
    await tl.step_phone(phone, state, settings)
    assert "служебный чат «Telegram»" in phone.replies[0]
    assert "/resend через 60 с" in phone.replies[0] and "по SMS" in phone.replies[0]

    resend = Msg("/resend")
    await tl.resend_code(resend, state)
    assert FakeClient.instances[-1].sends == 2
    assert "по SMS на этот номер" in resend.replies[0]
    assert await state.get_state() == tl.TgLogin.code.state


async def test_email_setup_required_stops_flow(env, monkeypatch):
    state, settings, collector = env
    monkeypatch.setattr(FakeClient, "sent_type", auth.SentCodeTypeSetUpEmailRequired())
    await tl.cmd_tglogin(Msg("/tglogin"), state, settings, collector)
    phone = Msg("+79001234567")
    await tl.step_phone(phone, state, settings)
    assert "Почта для входа" in phone.replies[0]
    assert await state.get_state() is None and FakeClient.instances[-1].disconnected


async def test_unexpected_send_error_is_reported(env, monkeypatch):
    state, settings, collector = env

    async def boom(self, phone):
        raise RuntimeError("logged in right after sending the code")

    monkeypatch.setattr(FakeClient, "send_code_request", boom)
    await tl.cmd_tglogin(Msg("/tglogin"), state, settings, collector)
    phone = Msg("+79001234567")
    await tl.step_phone(phone, state, settings)
    assert "RuntimeError" in phone.replies[0] and await state.get_state() is None


class FakeQR:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)  # per wait(): "timeout", "ok", "2fa"
        self.url = "tg://login?token=abc"
        self.recreated = 0

    async def recreate(self):
        self.recreated += 1
        self.url = f"tg://login?token=abc{self.recreated}"

    async def wait(self, timeout=None):
        import asyncio

        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            raise asyncio.TimeoutError
        if outcome == "2fa":
            raise errors.SessionPasswordNeededError(request=None)


class FakeBot:
    def __init__(self):
        self.photos, self.messages, self.deleted = [], [], []

    async def send_photo(self, chat_id, photo, caption=None):
        self.photos.append(caption)
        return SimpleNamespace(message_id=len(self.photos))

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


def qr_msg():
    m = Msg("/tgqr")
    m.chat = SimpleNamespace(id=1)
    return m


def test_qr_png():
    assert tl.qr_png("tg://login?token=abc").startswith(b"\x89PNG")


async def test_qr_login_refreshes_and_succeeds(env, monkeypatch):
    state, settings, collector = env
    qr = FakeQR(["timeout", "ok"])

    async def qr_login(self):
        return qr

    monkeypatch.setattr(FakeClient, "qr_login", qr_login, raising=False)
    bot, msg = FakeBot(), qr_msg()
    await tl.cmd_tgqr(msg, bot, state, settings, collector)
    await next(iter(tl._tasks))
    assert qr.recreated == 1 and len(bot.photos) == 2 and "abc1" in bot.photos[1]
    assert bot.deleted == [1, 2]  # stale and final QR removed
    assert collector.telegram is not None and "Готово" in msg.replies[-1]
    assert await state.get_state() is None


async def test_qr_login_with_2fa_asks_password(env, monkeypatch):
    state, settings, collector = env

    async def qr_login(self):
        return FakeQR(["2fa"])

    monkeypatch.setattr(FakeClient, "qr_login", qr_login, raising=False)
    bot, msg = FakeBot(), qr_msg()
    await tl.cmd_tgqr(msg, bot, state, settings, collector)
    await next(iter(tl._tasks))
    assert await state.get_state() == tl.TgLogin.password.state
    password = Msg("secret")
    await tl.step_password(password, state, collector)
    assert collector.telegram is not None


async def test_qr_cancel_is_silent(env, monkeypatch):
    import asyncio

    state, settings, collector = env
    started = asyncio.Event()

    class SlowQR(FakeQR):
        async def wait(self, timeout=None):
            started.set()
            await asyncio.sleep(0.05)
            raise ConnectionError("disconnected")

    async def qr_login(self):
        return SlowQR([])

    monkeypatch.setattr(FakeClient, "qr_login", qr_login, raising=False)
    bot, msg = FakeBot(), qr_msg()
    await tl.cmd_tgqr(msg, bot, state, settings, collector)
    task = next(iter(tl._tasks))
    await started.wait()
    await tl.cancel_login(Msg("/cancel"), state)
    await task
    assert bot.messages == []
