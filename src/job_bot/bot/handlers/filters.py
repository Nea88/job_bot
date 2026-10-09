import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.bot.formatting import format_filter
from job_bot.bot.keyboards import (
    EXPERIENCES,
    SCHEDULES,
    SOURCES,
    FilterCb,
    WizardCb,
    choice_kb,
    filter_actions_kb,
    filters_kb,
    sources_kb,
)
from job_bot.db.models import Filter
from job_bot.db.repo import get_or_create_user, get_user, get_user_filter
from job_bot.sources.hh import HHSource

log = logging.getLogger(__name__)
router = Router()

MAX_FILTERS = 5
SKIP = "-"


class NewFilter(StatesGroup):
    name = State()
    keywords_any = State()
    keywords_all = State()
    exclude = State()
    city = State()
    schedule = State()
    experience = State()
    salary = State()
    sources = State()


def parse_words(text: str) -> list[str]:
    if text.strip() == SKIP:
        return []
    result: list[str] = []
    for word in text.split(","):
        word = " ".join(word.split()).lower()
        if word and word not in result:
            result.append(word)
    return result


def parse_salary(text: str) -> int | None:
    """'250000', '250к', '250k' and bare '250' (nobody means 250 ₽ a month) all mean 250 000."""
    text = text.lower().replace(" ", "").removesuffix("₽").removesuffix("руб")
    multiplier = 1
    if text.endswith(("к", "k", "т")):
        text, multiplier = text[:-1], 1000
    if not text.isdigit():
        return None
    value = int(text) * multiplier
    return value * 1000 if value < 1000 else value


@router.message(Command("filters"))
async def cmd_filters(message: Message, sm: async_sessionmaker) -> None:
    async with sm() as session:
        user = await get_user(session, message.from_user.id)
        filters = user.filters if user else []
    text = "Твои фильтры:" if filters else "Фильтров пока нет."
    await message.answer(text, reply_markup=filters_kb(filters))


@router.callback_query(FilterCb.filter(F.action == "show"))
async def show_filter(call: CallbackQuery, callback_data: FilterCb, sm: async_sessionmaker) -> None:
    async with sm() as session:
        f = await get_user_filter(session, call.from_user.id, callback_data.filter_id)
    if f is None:
        await call.answer("Фильтр не найден", show_alert=True)
        return
    await call.message.answer(format_filter(f), reply_markup=filter_actions_kb(f))
    await call.answer()


@router.callback_query(FilterCb.filter(F.action == "delete"))
async def delete_filter(call: CallbackQuery, callback_data: FilterCb, sm: async_sessionmaker) -> None:
    async with sm() as session:
        f = await get_user_filter(session, call.from_user.id, callback_data.filter_id)
        if f is not None:
            await session.delete(f)
            await session.commit()
    await call.message.edit_text("Фильтр удалён.")
    await call.answer()


# --- wizard -----------------------------------------------------------------


@router.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.callback_query(FilterCb.filter(F.action == "new"))
async def new_filter(call: CallbackQuery, state: FSMContext, sm: async_sessionmaker) -> None:
    async with sm() as session:
        user = await get_user(session, call.from_user.id)
        if user and len(user.filters) >= MAX_FILTERS:
            await call.answer(f"Максимум {MAX_FILTERS} фильтров", show_alert=True)
            return
    await state.clear()
    await state.set_state(NewFilter.name)
    await call.message.answer("Создаём фильтр (/cancel — отменить).\n\nКак его назвать? Например: «Python backend».")
    await call.answer()


@router.message(NewFilter.name, F.text)
async def step_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(NewFilter.keywords_any)
    await message.answer(
        "Ключевые слова — достаточно <b>любого</b> из них, через запятую.\n"
        "Например: <code>python, django, fastapi</code>\n"
        "Звёздочка в конце — совпадение по началу слова: <code>разработ*</code>"
    )


@router.message(NewFilter.keywords_any, F.text)
async def step_any(message: Message, state: FSMContext) -> None:
    words = parse_words(message.text)
    if not words:
        await message.answer("Нужно хотя бы одно ключевое слово.")
        return
    await state.update_data(keywords_any=words)
    await state.set_state(NewFilter.keywords_all)
    await message.answer(f"Слова, которые должны быть <b>все</b> (через запятую), или {SKIP} чтобы пропустить.")


@router.message(NewFilter.keywords_all, F.text)
async def step_all(message: Message, state: FSMContext) -> None:
    await state.update_data(keywords_all=parse_words(message.text))
    await state.set_state(NewFilter.exclude)
    await message.answer(f"Слова-исключения (например: <code>1с, senior, стажёр</code>) или {SKIP}.")


@router.message(NewFilter.exclude, F.text)
async def step_exclude(message: Message, state: FSMContext) -> None:
    await state.update_data(exclude=parse_words(message.text))
    await state.set_state(NewFilter.city)
    await message.answer(f"Город (для удалёнки не важен) или {SKIP} — любой.")


@router.message(NewFilter.city, F.text)
async def step_city(message: Message, state: FSMContext, hh: HHSource) -> None:
    text = message.text.strip()
    city, area_id = None, None
    if text != SKIP:
        city = text
        try:
            resolved = await hh.resolve_area(text)
        except Exception:
            log.exception("hh area lookup failed")
            resolved = None
        if resolved:
            area_id, city = resolved
    await state.update_data(city=city, hh_area_id=area_id)
    await state.set_state(NewFilter.schedule)
    await message.answer("Формат работы:", reply_markup=choice_kb("schedule", SCHEDULES))


@router.callback_query(NewFilter.schedule, WizardCb.filter(F.field == "schedule"))
async def step_schedule(call: CallbackQuery, callback_data: WizardCb, state: FSMContext) -> None:
    await state.update_data(schedule=None if callback_data.value == "any" else callback_data.value)
    await state.set_state(NewFilter.experience)
    await call.message.edit_text(f"Формат: {SCHEDULES[callback_data.value]}")
    await call.message.answer("Опыт:", reply_markup=choice_kb("experience", EXPERIENCES))
    await call.answer()


@router.callback_query(NewFilter.experience, WizardCb.filter(F.field == "experience"))
async def step_experience(call: CallbackQuery, callback_data: WizardCb, state: FSMContext) -> None:
    await state.update_data(experience=None if callback_data.value == "any" else callback_data.value)
    await state.set_state(NewFilter.salary)
    await call.message.edit_text(f"Опыт: {EXPERIENCES[callback_data.value]}")
    await call.message.answer(f"Минимальная зарплата в рублях, например <code>300000</code> или <code>300к</code>, или {SKIP}.")
    await call.answer()


@router.message(NewFilter.salary, F.text)
async def step_salary(message: Message, state: FSMContext) -> None:
    text = message.text.strip().lower().replace(" ", "")
    if text == SKIP:
        salary = None
    elif (salary := parse_salary(text)) is None:
        await message.answer(f"Нужно число, например 250000 или 250к, или {SKIP}.")
        return
    selected = list(SOURCES)
    await state.update_data(salary_min=salary, sources=selected)
    await state.set_state(NewFilter.sources)
    await message.answer("Источники:", reply_markup=sources_kb(selected))


@router.callback_query(NewFilter.sources, WizardCb.filter(F.field == "source"))
async def step_sources(
    call: CallbackQuery, callback_data: WizardCb, state: FSMContext, sm: async_sessionmaker
) -> None:
    data = await state.get_data()
    selected: list[str] = data["sources"]
    if callback_data.value != "done":
        if callback_data.value in selected:
            selected.remove(callback_data.value)
        else:
            selected.append(callback_data.value)
        await state.update_data(sources=selected)
        await call.message.edit_reply_markup(reply_markup=sources_kb(selected))
        await call.answer()
        return

    if not selected:
        await call.answer("Выбери хотя бы один источник", show_alert=True)
        return

    async with sm() as session:
        user = await get_or_create_user(session, call.from_user.id)
        f = Filter(
            user_id=user.id,
            name=data["name"],
            keywords_any=data["keywords_any"],
            keywords_all=data["keywords_all"],
            exclude=data["exclude"],
            city=data["city"],
            hh_area_id=data["hh_area_id"],
            schedule=data["schedule"],
            experience=data["experience"],
            salary_min=data["salary_min"],
            sources=selected,
        )
        session.add(f)
        await session.commit()
    await state.clear()
    await call.message.edit_text(
        "Фильтр сохранён ✅\n\n" + format_filter(f) + "\n\nНовые вакансии придут при следующем сборе (раз в час)."
    )
    await call.answer()
