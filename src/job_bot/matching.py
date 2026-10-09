"""Single source of truth for "does this vacancy match this filter".

Server-side search params only narrow down what sources return; every vacancy is
re-checked here so all sources obey identical rules.
"""

import re
from functools import lru_cache
from typing import Protocol

from job_bot.sources.base import SearchQuery


class FilterLike(Protocol):
    keywords_any: list[str]
    keywords_all: list[str]
    exclude: list[str]
    city: str | None
    hh_area_id: str | None
    schedule: str | None
    experience: str | None
    salary_min: int | None
    currency: str
    sources: list[str]


class VacancyLike(Protocol):
    source: str
    title: str
    description: str
    area: str | None
    schedule: str | None
    experience: str | None
    salary_from: int | None
    salary_to: int | None
    currency: str | None


@lru_cache(maxsize=1024)
def _pattern(keyword: str) -> re.Pattern:
    """Whole-word match; a trailing * makes it a prefix match (разработ* -> разработчика)."""
    keyword = keyword.lower().strip()
    if keyword.endswith("*"):
        return re.compile(r"(?<!\w)" + re.escape(keyword[:-1]))
    return re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)")


def contains(text: str, keyword: str) -> bool:
    return bool(_pattern(keyword).search(text))


def matches(f: FilterLike, v: VacancyLike) -> bool:
    if f.sources and v.source not in f.sources:
        return False

    text = f"{v.title}\n{v.description}".lower()
    if f.keywords_any and not any(contains(text, k) for k in f.keywords_any):
        return False
    if not all(contains(text, k) for k in f.keywords_all):
        return False
    if any(contains(text, k) for k in f.exclude):
        return False

    formats = set(v.schedule.split(",")) if v.schedule else set()
    if f.schedule and formats and f.schedule not in formats:
        return False
    if f.schedule == "remote" and not formats:
        # unknown format: only keep explicitly remote posts
        return False

    if f.city and "remote" not in formats and v.area and f.city.lower() not in v.area.lower():
        return False

    if f.experience and v.experience and f.experience != v.experience:
        return False

    if f.salary_min and (v.currency is None or v.currency == f.currency):
        top = max(s for s in (v.salary_from, v.salary_to, 0) if s is not None)
        if top and top < f.salary_min:
            return False

    return True


def query_for(f: FilterLike) -> SearchQuery:
    return SearchQuery(
        keywords_any=tuple(f.keywords_any),
        keywords_all=tuple(f.keywords_all),
        exclude=tuple(f.exclude),
        hh_area_id=f.hh_area_id,
        schedule=f.schedule,
        experience=f.experience,
        salary_min=f.salary_min,
    )
