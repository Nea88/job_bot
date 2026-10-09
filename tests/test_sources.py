import json
from datetime import datetime
from pathlib import Path

from job_bot.sources import habr, hh
from job_bot.sources.base import SearchQuery
from job_bot.sources.telegram import looks_like_vacancy, parse_message

FIXTURES = Path(__file__).parent / "fixtures"


def test_hh_build_text():
    q = SearchQuery(keywords_any=("python", "data engineer"), keywords_all=("django",), exclude=("1с",))
    assert hh.build_text(q) == '(python OR "data engineer") AND django NOT 1с'


def test_hh_parse_item():
    item = {
        "id": "123",
        "name": "Python developer",
        "alternate_url": "https://hh.ru/vacancy/123",
        "employer": {"name": "Acme"},
        "salary": {"from": 200000, "to": None, "currency": "RUR"},
        "area": {"name": "Москва"},
        "schedule": {"id": "fullDay"},
        "work_format": [{"id": "REMOTE"}, {"id": "HYBRID"}],
        "experience": {"id": "between3And6"},
        "snippet": {"requirement": "Опыт с <highlighttext>Python</highlighttext> от 3 лет", "responsibility": None},
        "published_at": "2026-10-09T12:00:00+0300",
    }
    v = hh.parse_item(item)
    assert v.schedule == "remote,hybrid"
    assert v.salary_from == 200000 and v.salary_to is None
    assert v.published_at == datetime(2026, 10, 9, 9, 0)
    assert "Python" in v.description


def test_habr_parse_real_response():
    items = json.loads((FIXTURES / "habr_list.json").read_text())["list"]
    vacancies = [habr.parse_item(i) for i in items]
    assert all(v.url.startswith("https://career.habr.com/vacancies/") for v in vacancies)
    assert all(v.published_at is not None and v.published_at.tzinfo is None for v in vacancies)
    with_salary = [v for v in vacancies if v.salary_to]
    assert with_salary and with_salary[0].currency == "RUR"
    assert any(v.schedule == "remote" for v in vacancies)


def test_habr_parse_description():
    html = '<div class="vacancy-description__text"><p>Делать <b>API</b></p></div>'
    assert "API" in habr.parse_description(html)


def test_telegram_heuristics():
    post = "Python Developer\nИщем бэкенд-разработчика, удалённо. Требования: Django, PostgreSQL. " + "x" * 200
    assert looks_like_vacancy(post)
    assert not looks_like_vacancy("Подписывайтесь на канал!")
    v = parse_message("jobs", 42, post, datetime(2026, 10, 9))
    assert v.title == "Python Developer"
    assert v.schedule == "remote"
    assert v.url == "https://t.me/jobs/42"


def test_content_hash_cross_source():
    a = hh.parse_item({"id": "1", "name": "Python  Developer", "employer": {"name": "Acme"}})
    b = habr.parse_item({"id": 2, "title": "python developer", "company": {"title": "ACME"}})
    assert a.content_hash == b.content_hash


async def test_hh_search_uses_work_format():
    import httpx

    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"items": [], "pages": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await hh.HHSource(http, "Bot/1.0 (a@b.ru)").search(SearchQuery(keywords_any=("ios",), schedule="remote"), datetime(2026, 10, 9))
    assert seen["work_format"] == "REMOTE"
    assert "schedule" not in seen


async def test_hh_requires_real_user_agent():
    import httpx
    import pytest

    from job_bot.sources.base import SourceUnavailable

    async with httpx.AsyncClient() as http:
        for ua in (None, "job-bot/0.1 (you@example.com)", "job-bot"):
            with pytest.raises(SourceUnavailable):
                await hh.HHSource(http, ua).search(SearchQuery(keywords_any=("ios",)), datetime(2026, 10, 9))


async def test_hh_blacklisted_user_agent():
    import httpx
    import pytest

    from job_bot.sources.base import SourceUnavailable

    def handler(request):
        return httpx.Response(400, json={"errors": [{"value": "blacklisted", "type": "bad_user_agent"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SourceUnavailable):
            await hh.HHSource(http, "Bot/1.0 (a@b.ru)").search(SearchQuery(keywords_any=("ios",)), datetime(2026, 10, 9))


async def test_hh_captcha_without_token_is_actionable():
    import httpx
    import pytest

    from job_bot.sources.base import SourceUnavailable

    def handler(request):
        return httpx.Response(403, json={"errors": [{"type": "forbidden"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SourceUnavailable, match="hh_access_token"):
            await hh.HHSource(http, "Bot/1.0 (a@b.ru)").search(SearchQuery(keywords_any=("ios",)), datetime(2026, 10, 9))
