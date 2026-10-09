import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

from selectolax.lexbor import LexborHTMLParser as HTMLParser


@dataclass
class RawVacancy:
    source: str
    external_id: str
    url: str
    title: str
    company: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    area: str | None = None
    schedule: str | None = None  # comma separated subset of remote,hybrid,office
    experience: str | None = None
    description: str = ""
    published_at: datetime | None = None  # naive UTC

    @property
    def content_hash(self) -> str:
        """Cross-source dedup key: same title at the same company is the same vacancy."""
        if self.company:
            key = f"{_norm(self.title)}|{_norm(self.company)}"
        elif len(self.description) >= 100:
            key = _norm(self.description[:500])
        else:
            # too little text to recognise a duplicate: never collapse distinct vacancies
            key = f"{self.source}:{self.external_id}"
        return hashlib.sha1(key.encode()).hexdigest()

    def as_model_kwargs(self) -> dict:
        data = asdict(self)
        data["content_hash"] = self.content_hash
        return data


@dataclass(frozen=True)
class SearchQuery:
    """Server-side search parameters derived from a filter; identical queries are fetched once."""

    keywords_any: tuple[str, ...] = ()
    keywords_all: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    hh_area_id: str | None = None
    schedule: str | None = None
    experience: str | None = None
    salary_min: int | None = None

    @property
    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class SourceUnavailable(Exception):
    """The source cannot work at all (misconfiguration, ban): skip it for this run without retrying."""


class Source(Protocol):
    name: str

    async def search(self, query: SearchQuery, since: datetime) -> list[RawVacancy]: ...

    async def enrich(self, raw: RawVacancy) -> RawVacancy:
        """Fetch the full description; called only for vacancies not yet in the DB."""
        ...


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = HTMLParser(html).text(separator="\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
