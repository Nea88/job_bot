import asyncio
import logging
from datetime import datetime

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from job_bot.db.models import to_naive_utc
from job_bot.sources.base import RawVacancy, SearchQuery

log = logging.getLogger(__name__)

HABR = "https://career.habr.com"
MAX_PAGES = 3
REQUEST_DELAY = 1.0

# hh experience code -> Habr qualification ids (1 intern, 3 junior, 4 middle, 5 senior, 6 lead)
QUALIFICATION_MAP = {
    "noExperience": ["1", "3"],
    "between1And3": ["3", "4"],
    "between3And6": ["4", "5"],
    "moreThan6": ["5", "6"],
}
CURRENCY_MAP = {"rur": "RUR", "usd": "USD", "eur": "EUR"}


def parse_item(item: dict) -> RawVacancy:
    salary = item.get("salary") or {}
    skills = [s["title"] for s in item.get("skills") or []]
    locations = [loc["title"] for loc in item.get("locations") or []]
    published = (item.get("publishedDate") or {}).get("date")
    return RawVacancy(
        source="habr",
        external_id=str(item["id"]),
        url=HABR + item.get("href", f"/vacancies/{item['id']}"),
        title=item["title"],
        company=(item.get("company") or {}).get("title"),
        salary_from=salary.get("from"),
        salary_to=salary.get("to"),
        currency=CURRENCY_MAP.get((salary.get("currency") or "").lower()),
        area=", ".join(locations) or None,
        schedule="remote" if item.get("remoteWork") else None,
        description="Навыки: " + ", ".join(skills) if skills else "",
        published_at=to_naive_utc(datetime.fromisoformat(published)) if published else None,
    )


def parse_description(html: str) -> str:
    node = HTMLParser(html).css_first(".vacancy-description__text")
    return node.text(separator="\n").strip() if node else ""


class HabrSource:
    name = "habr"

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def search(self, query: SearchQuery, since: datetime) -> list[RawVacancy]:
        # Habr search is a plain full-text query with no OR, so run one search per "any" keyword;
        # precise any/all/exclude logic is applied afterwards by matching.py
        if query.keywords_all:
            texts = [" ".join(query.keywords_all)]
        else:
            texts = list(query.keywords_any[:3]) or [""]
        params: list[tuple[str, str]] = [("sort", "date"), ("type", "all")]
        if query.schedule == "remote":
            params.append(("remote", "true"))
        if query.salary_min:
            params.append(("salary", str(query.salary_min)))
        for qid in QUALIFICATION_MAP.get(query.experience or "", []):
            params.append(("qid[]", qid))

        result: list[RawVacancy] = []
        for text in texts:
            result.extend(await self._search_text([("q", text), *params], since))
        return result

    async def _search_text(self, params: list[tuple[str, str]], since: datetime) -> list[RawVacancy]:
        result: list[RawVacancy] = []
        for page in range(1, MAX_PAGES + 1):
            resp = await self._http.get(
                f"{HABR}/api/frontend/vacancies",
                params=[*params, ("page", str(page))],
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            items = resp.json().get("list") or []
            fresh = [parse_item(i) for i in items]
            result.extend(v for v in fresh if v.published_at is None or v.published_at >= since)
            # sorted by date: stop once we reach vacancies older than the last run
            if not items or any(v.published_at and v.published_at < since for v in fresh):
                break
            await asyncio.sleep(REQUEST_DELAY)
        return result

    async def enrich(self, raw: RawVacancy) -> RawVacancy:
        await asyncio.sleep(REQUEST_DELAY)
        try:
            resp = await self._http.get(raw.url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("habr: failed to fetch %s: %s", raw.url, e)
            return raw
        description = parse_description(resp.text)
        if description:
            raw.description = description + ("\n\n" + raw.description if raw.description else "")
        return raw
