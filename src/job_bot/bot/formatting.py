from html import escape

from job_bot.bot.keyboards import EXPERIENCES, SCHEDULES, SOURCES
from job_bot.db.models import Filter, Vacancy
from job_bot.interview.builder import MarketProfile

CURRENCY_SIGNS = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸"}
DIFFICULTY = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}


def format_salary(v: Vacancy) -> str | None:
    if not v.salary_from and not v.salary_to:
        return None
    sign = CURRENCY_SIGNS.get(v.currency or "", v.currency or "")
    if v.salary_from and v.salary_to:
        return f"{v.salary_from:,} – {v.salary_to:,} {sign}".replace(",", " ")
    if v.salary_from:
        return f"от {v.salary_from:,} {sign}".replace(",", " ")
    return f"до {v.salary_to:,} {sign}".replace(",", " ")


def format_vacancy(v: Vacancy, filter_name: str) -> str:
    meta = [escape(x) for x in (v.company, v.area) if x]
    if v.schedule:
        meta.append(", ".join(SCHEDULES.get(s, s) for s in v.schedule.split(",")))
    lines = [f"<b>{escape(v.title)}</b>"]
    if meta:
        lines.append(" · ".join(meta))
    if salary := format_salary(v):
        lines.append(f"💰 {salary}")
    if v.source == "telegram" and v.description:
        lines.append("")
        lines.append(escape(v.description[:600]) + ("…" if len(v.description) > 600 else ""))
    lines.append("")
    lines.append(f"<i>{SOURCES.get(v.source, v.source)} · фильтр «{escape(filter_name)}»</i>")
    return "\n".join(lines)


def format_digest(items: list[Vacancy], limit: int = 3800) -> str:
    out = f"…и ещё {len(items)} вакансий:\n"
    for v in items:
        line = f'• <a href="{escape(v.url)}">{escape(v.title)}</a>' + (f" — {escape(v.company)}" if v.company else "") + "\n"
        if len(out) + len(line) > limit:
            break
        out += line
    return out


def format_filter(f: Filter) -> str:
    def words(items: list[str]) -> str:
        return ", ".join(escape(i) for i in items) if items else "—"

    salary = f"от {f.salary_min:,}".replace(",", " ") + " ₽" if f.salary_min else "—"
    return (
        f"<b>{escape(f.name)}</b>\n"
        f"Любое из: {words(f.keywords_any)}\n"
        f"Все из: {words(f.keywords_all)}\n"
        f"Исключить: {words(f.exclude)}\n"
        f"Город: {escape(f.city) if f.city else '—'}\n"
        f"Формат: {SCHEDULES.get(f.schedule or 'any')}\n"
        f"Опыт: {EXPERIENCES.get(f.experience or 'any')}\n"
        f"Зарплата: {salary}\n"
        f"Источники: {', '.join(SOURCES.get(s, s) for s in f.sources)}"
    )


def format_profile(p: MarketProfile, filter_name: str) -> str:
    lines = [
        f"<b>Рынок по фильтру «{escape(filter_name)}»</b>",
        f"Вакансий за 30 дней: {p.vacancy_count} · типичный уровень: {p.seniority}",
        "",
    ]
    for s in p.skills:
        lines.append(f"{s.share:>4.0%}  {escape(s.skill)}" + (f"  (обязательно в {s.must_share:.0%})" if s.must_share else ""))
    return "\n".join(lines)


def format_question(payload: dict, idx: int, show_answer: bool) -> str:
    questions = payload["questions"]
    q = questions[idx]
    text = (
        f"<b>Вопрос {idx + 1}/{len(questions)}</b> · {escape(q['skill'])} {DIFFICULTY.get(q['difficulty'], '')}\n\n"
        f"{escape(q['question'])}"
    )
    if show_answer:
        text += "\n\n<b>Что должно прозвучать:</b>\n" + "\n".join(f"• {escape(p)}" for p in q["answer_points"])
    return text
