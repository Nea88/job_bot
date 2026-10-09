import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.bot.formatting import format_profile, format_question
from job_bot.bot.keyboards import FilterCb, InterviewCb, filters_kb, question_kb
from job_bot.db.models import Filter, Interview
from job_bot.db.repo import get_user, get_user_filter
from job_bot.interview.builder import MIN_VACANCIES, InterviewService, market_profile
from job_bot.llm.ollama import LLMError

log = logging.getLogger(__name__)
router = Router()

# keep references so background tasks are not garbage collected mid-flight
_tasks: set[asyncio.Task] = set()
_generating: set[int] = set()  # filter ids with an interview in progress


async def send_interview(bot: Bot, chat_id: int, interviews: InterviewService, f: Filter) -> None:
    """Generate an interview (slow, local LLM) and send its first question."""
    if f.id in _generating:
        await bot.send_message(chat_id, "Интервью по этому фильтру уже готовится, подожди немного.")
        return
    _generating.add(f.id)
    try:
        interview = await interviews.generate(f)
    except LLMError:
        log.exception("interview generation failed")
        await bot.send_message(chat_id, "Не получилось сгенерировать интервью: LLM недоступна. Попробуй позже.")
        return
    finally:
        _generating.discard(f.id)
    if interview is None:
        await bot.send_message(
            chat_id,
            f"Пока мало данных: нужно хотя бы {MIN_VACANCIES} проанализированных вакансий по фильтру «{f.name}». "
            "Загляни позже — анализ идёт в фоне.",
        )
        return
    total = len(interview.payload["questions"])
    await bot.send_message(
        chat_id,
        f"🎯 Интервью: {interview.payload['role']}, {interview.payload['seniority']} "
        f"(по {interview.payload['vacancy_count']} вакансиям). Вопросов: {total}.",
    )
    await bot.send_message(
        chat_id,
        format_question(interview.payload, 0, show_answer=False),
        reply_markup=question_kb(interview.id, 0, total, answered=False),
    )


async def _pick_filter(message: Message, sm: async_sessionmaker, action: str) -> Filter | None:
    """Returns the only filter, or shows a picker and returns None."""
    async with sm() as session:
        user = await get_user(session, message.from_user.id)
        filters = user.filters if user else []
    if not filters:
        await message.answer("Сначала создай фильтр: /filters")
        return None
    if len(filters) == 1:
        return filters[0]
    await message.answer("По какому фильтру?", reply_markup=filters_kb(filters, action=action, allow_new=False))
    return None


@router.message(Command("interview"))
async def cmd_interview(message: Message, bot: Bot, sm: async_sessionmaker, interviews: InterviewService) -> None:
    f = await _pick_filter(message, sm, "interview")
    if f is not None:
        await _start_interview(bot, message.chat.id, interviews, f)


@router.message(Command("stats"))
async def cmd_stats(message: Message, sm: async_sessionmaker) -> None:
    f = await _pick_filter(message, sm, "stats")
    if f is not None:
        await _send_stats(message, sm, f)


@router.callback_query(FilterCb.filter(F.action == "interview"))
async def cb_interview(
    call: CallbackQuery, callback_data: FilterCb, bot: Bot, sm: async_sessionmaker, interviews: InterviewService
) -> None:
    async with sm() as session:
        f = await get_user_filter(session, call.from_user.id, callback_data.filter_id)
    if f is None:
        await call.answer("Фильтр не найден", show_alert=True)
        return
    await call.answer()
    await _start_interview(bot, call.message.chat.id, interviews, f)


@router.callback_query(FilterCb.filter(F.action == "stats"))
async def cb_stats(call: CallbackQuery, callback_data: FilterCb, sm: async_sessionmaker) -> None:
    async with sm() as session:
        f = await get_user_filter(session, call.from_user.id, callback_data.filter_id)
    if f is None:
        await call.answer("Фильтр не найден", show_alert=True)
        return
    await call.answer()
    await _send_stats(call.message, sm, f)


@router.callback_query(InterviewCb.filter())
async def cb_question(call: CallbackQuery, callback_data: InterviewCb, sm: async_sessionmaker) -> None:
    async with sm() as session:
        interview = await session.get(Interview, callback_data.interview_id)
        user = await get_user(session, call.from_user.id)
    if interview is None or user is None or interview.user_id != user.id:
        await call.answer("Интервью не найдено", show_alert=True)
        return
    total = len(interview.payload["questions"])
    idx = callback_data.idx
    if idx >= total:
        await call.answer()
        return
    if callback_data.answer:
        # reveal the answer in place
        await call.message.edit_text(
            format_question(interview.payload, idx, show_answer=True),
            reply_markup=question_kb(interview.id, idx, total, answered=True),
        )
    else:
        # drop the "next" button from the previous question and post the new one
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(
            format_question(interview.payload, idx, show_answer=False),
            reply_markup=question_kb(interview.id, idx, total, answered=False),
        )
        if idx + 1 == total:
            await call.message.answer("Это последний вопрос. Новое интервью — /interview")
    await call.answer()


async def _start_interview(bot: Bot, chat_id: int, interviews: InterviewService, f: Filter) -> None:
    await bot.send_message(chat_id, f"Собираю интервью по фильтру «{f.name}»… Локальная модель думает, это может занять пару минут.")
    task = asyncio.create_task(send_interview(bot, chat_id, interviews, f))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send_stats(message: Message, sm: async_sessionmaker, f: Filter) -> None:
    profile = await market_profile(sm, f)
    if not profile.skills:
        await message.answer(f"По фильтру «{f.name}» ещё нет проанализированных вакансий.")
        return
    await message.answer(format_profile(profile, f.name))
