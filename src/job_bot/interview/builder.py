import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.db.models import Filter, Interview, Vacancy, utcnow
from job_bot.llm.ollama import OllamaClient
from job_bot.llm.prompts import INTERVIEW_SYSTEM, INTERVIEW_USER
from job_bot.llm.schemas import InterviewPlan
from job_bot.matching import matches

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
TOP_SKILLS = 12
MIN_VACANCIES = 3
TECHNICAL_QUESTIONS = 10


@dataclass
class SkillStat:
    skill: str
    share: float  # fraction of vacancies mentioning the skill
    must_share: float  # fraction of vacancies where it is mandatory


@dataclass
class MarketProfile:
    vacancy_count: int
    role: str
    seniority: str
    skills: list[SkillStat]


def aggregate(vacancies: Iterable[Vacancy], top: int = TOP_SKILLS) -> MarketProfile:
    """Rank hard skills by how many vacancies ask for them; mandatory mentions weigh double."""
    count: Counter[str] = Counter()
    must: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    seniorities: Counter[str] = Counter()
    n = 0
    for v in vacancies:
        n += 1
        if v.role:
            roles[v.role] += 1
        if v.seniority and v.seniority != "unknown":
            seniorities[v.seniority] += 1
        for r in v.requirements:
            if r.kind != "hard":
                continue
            count[r.skill] += 1
            if r.level == "must":
                must[r.skill] += 1

    ranked = sorted(count, key=lambda s: (count[s] + must[s], count[s]), reverse=True)[:top]
    return MarketProfile(
        vacancy_count=n,
        role=roles.most_common(1)[0][0] if roles else "Software Engineer",
        seniority=seniorities.most_common(1)[0][0] if seniorities else "middle",
        skills=[SkillStat(s, count[s] / n, must[s] / n) for s in ranked] if n else [],
    )


async def market_profile(sm: async_sessionmaker, f: Filter, days: int = WINDOW_DAYS) -> MarketProfile:
    since = utcnow() - timedelta(days=days)
    async with sm() as session:
        rows = await session.scalars(
            select(Vacancy).where(
                Vacancy.analyzed_at.is_not(None),
                Vacancy.is_vacancy.is_(True),
                Vacancy.fetched_at >= since,
            )
        )
        return aggregate(v for v in rows if matches(f, v))


class InterviewService:
    def __init__(self, sm: async_sessionmaker, llm: OllamaClient):
        self._sm = sm
        self._llm = llm

    async def generate(self, f: Filter) -> Interview | None:
        """Returns None when there are too few analyzed vacancies for this filter."""
        profile = await market_profile(self._sm, f)
        if profile.vacancy_count < MIN_VACANCIES or not profile.skills:
            return None

        system_design = 2 if profile.seniority in ("middle", "senior", "lead") else 0
        total = TECHNICAL_QUESTIONS + system_design + 2
        prompt = INTERVIEW_USER.format(
            role=profile.role,
            seniority=profile.seniority,
            vacancy_count=profile.vacancy_count,
            skills="\n".join(f"- {s.skill} — {s.share:.0%}" for s in profile.skills),
            total=total,
            technical=TECHNICAL_QUESTIONS,
            system_design=f'- {system_design} по system design (skill = "system design");\n' if system_design else "",
        )
        plan = await self._llm.chat_json(INTERVIEW_SYSTEM, prompt, InterviewPlan, temperature=0.5)

        async with self._sm() as session:
            interview = Interview(
                user_id=f.user_id,
                filter_id=f.id,
                payload={
                    "role": profile.role,
                    "seniority": profile.seniority,
                    "vacancy_count": profile.vacancy_count,
                    "questions": [q.model_dump() for q in plan.questions],
                },
            )
            session.add(interview)
            await session.commit()
        return interview
