"""hirify.me: an aggregator without a public API.

robots.txt disallows /api/* and search/pagination query strings, so we only read what a crawler
may: the home page and the category landing pages (Nuxt SSR payload with ~15-20 fresh vacancies
each), plus the vacancy page's schema.org JobPosting for the full description.
"""

import asyncio
import json
import logging
import re
from datetime import datetime

import httpx

from job_bot.db.models import to_naive_utc
from job_bot.sources.base import RawVacancy, html_to_text

log = logging.getLogger(__name__)

HIRIFY = "https://hirify.me"
REQUEST_DELAY = 1.5
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-bot/0.1; +https://github.com/Nea88/job_bot)"}

WORK_FORMAT_MAP = {"remote": "remote", "hybrid": "hybrid", "onsite": "office", "office": "office"}
CURRENCY_MAP = {"RUB": "RUR"}
MONTHS_IN_PERIOD = {"month": 1, "year": 12}

_NUXT_DATA = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_JSON_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_CATEGORY_LINK = re.compile(r'href="/([a-z0-9-]+-jobs)"')
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef"}


def _resolve(data: list, value, depth: int = 0):
    """Nuxt payload (devalue) stores every value once and refers to it by index."""
    if not isinstance(value, int) or isinstance(value, bool) or depth > 8:
        return value
    node = data[value]
    if isinstance(node, dict):
        return {k: _resolve(data, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        if node and node[0] in _WRAPPERS and len(node) == 2:
            return _resolve(data, node[1], depth + 1)
        return [_resolve(data, v, depth + 1) for v in node]
    return node


def parse_listing(html: str) -> list[dict]:
    match = _NUXT_DATA.search(html)
    if not match:
        return []
    data = json.loads(match.group(1))
    return [
        _resolve(data, i)
        for i, node in enumerate(data)
        if isinstance(node, dict) and "slug" in node and "apply_url" in node and "title" in node
    ]


def parse_categories(html: str) -> list[str]:
    return sorted(set(_CATEGORY_LINK.findall(html)))


def _company(name: str | None) -> str | None:
    # hirify leaks unrendered placeholders like "%hirify_global%" instead of a company name
    if not name or re.fullmatch(r"%[^%]*%", name.strip()):
        return None
    return name.strip()


def _monthly(amount, period: str | None) -> int | None:
    months = MONTHS_IN_PERIOD.get(period or "month")
    if amount is None or months is None:
        return None
    return round(amount / months)


def parse_item(item: dict) -> RawVacancy:
    salary = item.get("salary") or {}
    period = salary.get("salary_period")
    formats = [WORK_FORMAT_MAP[f] for f in item.get("work_format") or [] if f in WORK_FORMAT_MAP]
    regions = [r.get("name") for r in item.get("regions") or [] if r.get("name")]
    details = []
    if grades := [g["name"] for g in item.get("grades") or [] if g.get("name")]:
        details.append("Грейд: " + ", ".join(grades))
    if specs := [s["name"] for s in item.get("specializations") or [] if s.get("name")]:
        details.append("Специализация: " + ", ".join(specs))
    if tags := [t["name"] for t in item.get("tags") or [] if t.get("name")]:
        details.append("Навыки: " + ", ".join(tags))
    created = item.get("created_at")
    return RawVacancy(
        source="hirify",
        external_id=str(item["id"]),
        url=f"{HIRIFY}/jobs/{item['slug']}",
        title=item["title"].strip(),
        company=_company(item.get("company_title")),
        salary_from=_monthly(salary.get("min"), period),
        salary_to=_monthly(salary.get("max"), period),
        currency=CURRENCY_MAP.get(salary.get("currency"), salary.get("currency")) if salary else None,
        area=", ".join(regions) or None,
        schedule=",".join(dict.fromkeys(formats)) or None,
        description="\n".join(details),
        published_at=to_naive_utc(datetime.fromisoformat(created.replace("Z", "+00:00"))) if created else None,
    )


def parse_job_posting(html: str) -> dict | None:
    for block in _JSON_LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


class HirifySource:
    """Not query-based: every run reads the latest vacancies across all categories."""

    name = "hirify"

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def fetch_latest(self) -> list[RawVacancy]:
        home = await self._get(HIRIFY + "/")
        pages = [home] if home else []
        # categories are discovered from the home page, so new ones are picked up automatically
        for category in parse_categories(home or ""):
            await asyncio.sleep(REQUEST_DELAY)
            if html := await self._get(f"{HIRIFY}/{category}"):
                pages.append(html)

        result: dict[str, RawVacancy] = {}
        for html in pages:
            for item in parse_listing(html):
                try:
                    raw = parse_item(item)
                except (KeyError, TypeError, ValueError) as e:
                    log.warning("hirify: skipping malformed item %s: %s", item.get("id"), e)
                    continue
                result[raw.external_id] = raw
        log.info("hirify: %d vacancies on %d pages", len(result), len(pages))
        return list(result.values())

    async def enrich(self, raw: RawVacancy) -> RawVacancy:
        await asyncio.sleep(REQUEST_DELAY)
        html = await self._get(raw.url)
        posting = parse_job_posting(html or "")
        if posting is None:
            return raw
        description = html_to_text(posting.get("description"))
        if description:
            raw.description = description + ("\n\n" + raw.description if raw.description else "")
        if not raw.company:
            raw.company = _company((posting.get("hiringOrganization") or {}).get("name"))
        return raw

    async def _get(self, url: str) -> str | None:
        try:
            resp = await self._http.get(url, headers=HEADERS)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as e:
            log.warning("hirify: failed to fetch %s: %s", url, e)
            return None
