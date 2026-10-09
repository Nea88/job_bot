import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from job_bot.db.models import Requirement, Sent, Vacancy, utcnow
from job_bot.llm.ollama import LLMError, OllamaClient
from job_bot.llm.prompts import EXTRACT_SYSTEM, EXTRACT_USER
from job_bot.llm.schemas import Extraction
from job_bot.skills import normalize_skill

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 6000


class Analyzer:
    """Background worker: extracts requirements from vacancies with the LLM, never blocking collection."""

    def __init__(self, sessionmaker: async_sessionmaker, llm: OllamaClient):
        self._sm = sessionmaker
        self._llm = llm
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._pending: set[int] = set()

    def enqueue(self, vacancy_ids: Iterable[int]) -> None:
        for vid in vacancy_ids:
            if vid not in self._pending:
                self._pending.add(vid)
                self._queue.put_nowait(vid)

    async def backfill(self) -> None:
        """Re-queue vacancies left unanalyzed (restart, LLM was down); only ones sent to someone."""
        async with self._sm() as session:
            ids = await session.scalars(
                select(Vacancy.id)
                .where(Vacancy.analyzed_at.is_(None), Vacancy.id.in_(select(Sent.vacancy_id)))
                .order_by(Vacancy.id.desc())
            )
            self.enqueue(ids)

    async def run(self) -> None:
        while True:
            vid = await self._queue.get()
            try:
                await self.analyze(vid)
            except LLMError as e:
                log.warning("analyze %s: LLM failed, will retry on backfill: %s", vid, e)
            except Exception:
                log.exception("analyze %s failed", vid)
            finally:
                self._pending.discard(vid)
                self._queue.task_done()

    async def analyze(self, vacancy_id: int) -> None:
        async with self._sm() as session:
            vacancy = await session.get(Vacancy, vacancy_id)
            if vacancy is None or vacancy.analyzed_at is not None:
                return
            result = await self._llm.chat_json(
                EXTRACT_SYSTEM,
                EXTRACT_USER.format(
                    title=vacancy.title,
                    company=vacancy.company or "—",
                    description=vacancy.description[:MAX_DESCRIPTION_CHARS],
                ),
                Extraction,
            )
            vacancy.is_vacancy = result.is_vacancy
            vacancy.role = result.role[:200]
            vacancy.seniority = result.seniority
            vacancy.requirements = _requirements(result)
            vacancy.analyzed_at = utcnow()
            await session.commit()
            log.info("analyzed %s: %d skills", vacancy_id, len(vacancy.requirements))


def _requirements(result: Extraction) -> list[Requirement]:
    seen: dict[tuple[str, str], Requirement] = {}
    for s in result.hard_skills:
        skill = normalize_skill(s.name)
        if not skill:
            continue
        existing = seen.get((skill, "hard"))
        if existing is None:
            seen[(skill, "hard")] = Requirement(skill=skill[:100], kind="hard", level=s.level)
        elif s.level == "must":
            existing.level = "must"
    for name in result.soft_skills:
        skill = " ".join(name.strip().split()).lower()
        if skill and (skill, "soft") not in seen:
            seen[(skill, "soft")] = Requirement(skill=skill[:100], kind="soft", level="nice")
    return list(seen.values())
