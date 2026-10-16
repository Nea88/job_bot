import re
from pathlib import Path

from job_bot.bot.commands import ADMIN_COMMANDS, USER_COMMANDS, help_text

HANDLERS = Path(__file__).parent.parent / "src/job_bot/bot/handlers"


def registered_commands() -> set[str]:
    names = {"start"}  # CommandStart()
    for path in HANDLERS.glob("*.py"):
        for args in re.findall(r"Command\(([^)]*)\)", path.read_text()):
            names |= set(re.findall(r'"([a-z_]+)"', args))
    return names


def test_menu_lists_every_handled_command():
    menu = {name for name, _ in USER_COMMANDS + ADMIN_COMMANDS}
    assert menu == registered_commands()


def test_menu_command_constraints():
    # Telegram: 1-32 chars of a-z0-9_, description 1-256 chars
    for name, description in USER_COMMANDS + ADMIN_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name)
        assert 1 <= len(description) <= 256


def test_help_hides_admin_commands():
    assert "/run_now" not in help_text(is_admin=False)
    assert "/run_now" in help_text(is_admin=True)
    assert "/cancel" in help_text(is_admin=False)


def test_help_is_valid_telegram_html():
    text = help_text(is_admin=True)
    # only tags Telegram supports may appear; "<username>" must arrive escaped
    assert re.findall(r"</?([a-z]+)", text) == ["b", "b"]
    assert "&lt;username&gt;" in text
