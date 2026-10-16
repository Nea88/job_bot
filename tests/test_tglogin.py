from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from telethon import errors

from job_bot.bot.handlers import telegram_login as tl


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, *args, password_required=True):
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
        return SimpleNamespace(phone_code_hash="hash")

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
