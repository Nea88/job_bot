from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from job_bot.db.models import Filter, Vacancy

SCHEDULES = {"remote": "Удалёнка", "hybrid": "Гибрид", "office": "Офис", "any": "Любой"}
EXPERIENCES = {
    "noExperience": "Без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "6+ лет",
    "any": "Любой",
}
SOURCES = {"hh": "hh.ru", "habr": "Habr Career", "telegram": "Telegram-каналы"}


class FilterCb(CallbackData, prefix="flt"):
    action: str  # show | delete | new | interview | stats
    filter_id: int = 0


class WizardCb(CallbackData, prefix="wz"):
    field: str  # schedule | experience | source
    value: str


class InterviewCb(CallbackData, prefix="iv"):
    interview_id: int
    idx: int
    answer: bool = False


def vacancy_kb(v: Vacancy, filter_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть", url=v.url)],
            [
                InlineKeyboardButton(
                    text="🎯 Интервью по фильтру",
                    callback_data=FilterCb(action="interview", filter_id=filter_id).pack(),
                )
            ],
        ]
    )


def filters_kb(filters: list[Filter], action: str = "show", allow_new: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for f in filters:
        kb.button(text=f.name, callback_data=FilterCb(action=action, filter_id=f.id))
    if allow_new:
        kb.button(text="➕ Новый фильтр", callback_data=FilterCb(action="new"))
    kb.adjust(1)
    return kb.as_markup()


def filter_actions_kb(f: Filter) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Интервью", callback_data=FilterCb(action="interview", filter_id=f.id))
    kb.button(text="📊 Навыки", callback_data=FilterCb(action="stats", filter_id=f.id))
    kb.button(text="🗑 Удалить", callback_data=FilterCb(action="delete", filter_id=f.id))
    kb.adjust(2, 1)
    return kb.as_markup()


def choice_kb(field: str, options: dict[str, str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value, label in options.items():
        kb.button(text=label, callback_data=WizardCb(field=field, value=value))
    kb.adjust(2)
    return kb.as_markup()


def sources_kb(selected: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value, label in SOURCES.items():
        mark = "✅" if value in selected else "▫️"
        kb.button(text=f"{mark} {label}", callback_data=WizardCb(field="source", value=value))
    kb.button(text="Готово", callback_data=WizardCb(field="source", value="done"))
    kb.adjust(1)
    return kb.as_markup()


def question_kb(interview_id: int, idx: int, total: int, answered: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not answered:
        kb.button(text="Показать ответ", callback_data=InterviewCb(interview_id=interview_id, idx=idx, answer=True))
    if idx + 1 < total:
        kb.button(text="Следующий →", callback_data=InterviewCb(interview_id=interview_id, idx=idx + 1))
    kb.adjust(1)
    return kb.as_markup()
