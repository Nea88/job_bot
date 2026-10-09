import asyncio
import logging
from datetime import datetime

import httpx

from job_bot.db.models import to_naive_utc
from job_bot.sources.base import RawVacancy, SearchQuery, SourceUnavailable, html_to_text

log = logging.getLogger(__name__)

HH_API = "https://api.hh.ru"
MAX_PAGES = 5
PER_PAGE = 100
REQUEST_DELAY = 0.3

WORK_FORMAT_MAP = {"REMOTE": "remote", "HYBRID": "hybrid", "ON_SITE": "office"}
SCHEDULE_MAP = {"remote": "remote", "fullDay": "office", "shift": "office", "flyInFly": "office"}
# filter schedule -> hh `work_format` search param (the old `schedule` param is deprecated and rejected)
WORK_FORMAT_PARAM = {v: k for k, v in WORK_FORMAT_MAP.items()}


def build_text(query: SearchQuery) -> str:
    """hh.ru search language: (a OR b) AND c NOT d."""

    def q(word: str) -> str:
        return f'"{word}"' if " " in word else word

    parts = []
    if query.keywords_any:
        parts.append("(" + " OR ".join(q(w) for w in query.keywords_any) + ")")
    parts.extend(q(w) for w in query.keywords_all)
    text = " AND ".join(parts)
    for word in query.exclude:
        text += f" NOT {q(word)}"
    return text.strip()


def parse_item(item: dict) -> RawVacancy:
    salary = item.get("salary_range") or item.get("salary") or {}
    formats = [WORK_FORMAT_MAP[f["id"]] for f in item.get("work_format") or [] if f.get("id") in WORK_FORMAT_MAP]
    if not formats:
        mapped = SCHEDULE_MAP.get((item.get("schedule") or {}).get("id"))
        formats = [mapped] if mapped else []
    snippet = item.get("snippet") or {}
    description = "\n".join(
        html_to_text(snippet.get(k)) for k in ("requirement", "responsibility") if snippet.get(k)
    )
    published = item.get("published_at")
    return RawVacancy(
        source="hh",
        external_id=str(item["id"]),
        url=item.get("alternate_url") or f"https://hh.ru/vacancy/{item['id']}",
        title=item["name"],
        company=(item.get("employer") or {}).get("name"),
        salary_from=salary.get("from"),
        salary_to=salary.get("to"),
        currency=salary.get("currency"),
        area=(item.get("area") or {}).get("name"),
        schedule=",".join(formats) or None,
        experience=(item.get("experience") or {}).get("id"),
        description=description,
        published_at=to_naive_utc(datetime.strptime(published, "%Y-%m-%dT%H:%M:%S%z")) if published else None,
    )


USER_AGENT_HELP = (
    'set hh_user_agent to "AppName/1.0 (your@email)"; hh.ru blacklists empty and placeholder values'
)


def is_placeholder_user_agent(user_agent: str | None) -> bool:
    return not user_agent or "example.com" in user_agent or "@" not in user_agent


class HHSource:
    name = "hh"

    def __init__(self, http: httpx.AsyncClient, user_agent: str | None, access_token: str | None = None):
        self._http = http
        self._user_agent = user_agent
        self._headers = {"HH-User-Agent": user_agent or "", "User-Agent": user_agent or ""}
        if access_token:
            self._headers["Authorization"] = f"Bearer {access_token}"

    async def search(self, query: SearchQuery, since: datetime) -> list[RawVacancy]:
        if is_placeholder_user_agent(self._user_agent):
            raise SourceUnavailable(USER_AGENT_HELP)
        params: dict = {
            "text": build_text(query),
            "per_page": PER_PAGE,
            "order_by": "publication_time",
            "date_from": since.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        }
        if query.hh_area_id:
            params["area"] = query.hh_area_id
        if query.experience:
            params["experience"] = query.experience
        if query.schedule in WORK_FORMAT_PARAM:
            params["work_format"] = WORK_FORMAT_PARAM[query.schedule]
        if query.salary_min:
            params["salary"] = query.salary_min

        result: list[RawVacancy] = []
        for page in range(MAX_PAGES):
            resp = await self._http.get(
                f"{HH_API}/vacancies", params={**params, "page": page}, headers=self._headers
            )
            if resp.is_client_error:
                # hh explains rejected params in the body, e.g. {"errors": [{"type": "bad_argument", "value": "..."}]}
                if "bad_user_agent" in resp.text:
                    raise SourceUnavailable(f"User-Agent {self._user_agent!r} rejected: {USER_AGENT_HELP}")
                if resp.status_code == 403 and "Authorization" not in self._headers:
                    # anonymous search gets a captcha after the first request
                    raise SourceUnavailable(
                        "hh.ru requires an application token for vacancy search: register an app on "
                        "https://dev.hh.ru, run scripts/hh_app_token.py and set hh_access_token"
                    )
                log.error("hh rejected search %s: %s", resp.url, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            result.extend(parse_item(item) for item in data.get("items", []))
            if page + 1 >= data.get("pages", 0):
                break
            await asyncio.sleep(REQUEST_DELAY)
        return result

    async def enrich(self, raw: RawVacancy) -> RawVacancy:
        await asyncio.sleep(REQUEST_DELAY)
        try:
            resp = await self._http.get(f"{HH_API}/vacancies/{raw.external_id}", headers=self._headers)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("hh: failed to fetch vacancy %s: %s", raw.external_id, e)
            return raw
        data = resp.json()
        description = html_to_text(data.get("description"))
        skills = [s["name"] for s in data.get("key_skills") or []]
        if skills:
            description += "\n\nКлючевые навыки: " + ", ".join(skills)
        if description:
            raw.description = description
        return raw

    async def resolve_area(self, text: str) -> tuple[str, str] | None:
        """City name -> (hh area id, canonical name)."""
        resp = await self._http.get(
            f"{HH_API}/suggests/areas", params={"text": text}, headers=self._headers
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items") or []
        if not items:
            return None
        return str(items[0]["id"]), items[0]["text"]
