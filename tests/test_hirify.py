from pathlib import Path

from job_bot.sources import hirify

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_real_listing():
    html = (FIXTURES / "hirify_listing.html").read_text()
    items = hirify.parse_listing(html)
    assert len(items) >= 10
    vacancies = [hirify.parse_item(i) for i in items]
    assert all(v.url.startswith("https://hirify.me/jobs/") for v in vacancies)
    assert all(v.published_at is not None and v.published_at.tzinfo is None for v in vacancies)
    assert all(v.schedule is None or set(v.schedule.split(",")) <= {"remote", "hybrid", "office"} for v in vacancies)
    assert any(v.description for v in vacancies)
    assert hirify.parse_categories(html) == ["mobile-development-jobs", "qa-jobs"]


def test_salary_is_monthly():
    item = {
        "id": 1, "slug": "1-ios", "title": " iOS Developer ", "apply_url": None,
        "salary": {"currency": "USD", "salary_period": "year", "min": 120000, "max": None},
        "work_format": ["onsite", "remote"], "regions": [{"name": "США"}],
        "grades": [{"name": "senior"}], "tags": [{"name": "swift"}],
        "created_at": "2026-10-09T15:03:52.000000Z",
    }
    v = hirify.parse_item(item)
    assert v.title == "iOS Developer"
    assert (v.salary_from, v.salary_to, v.currency) == (10000, None, "USD")
    assert v.schedule == "office,remote"
    assert "swift" in v.description and "senior" in v.description

    item["salary"] = {"currency": "RUB", "salary_period": "hour", "min": 2000, "max": 3000}
    v = hirify.parse_item(item)
    assert (v.salary_from, v.salary_to, v.currency) == (None, None, "RUR")


def test_parse_job_posting():
    posting = hirify.parse_job_posting((FIXTURES / "hirify_job.html").read_text())
    assert posting and posting["title"] and posting["hiringOrganization"]["name"] == "Keepgo"


def test_vacancies_without_company_do_not_collide():
    a = hirify.parse_item({"id": 1, "slug": "1-a", "title": "A", "apply_url": None})
    b = hirify.parse_item({"id": 2, "slug": "2-b", "title": "B", "apply_url": None})
    assert a.content_hash != b.content_hash


def test_placeholder_company_dropped():
    item = {"id": 3, "slug": "3-c", "title": "C", "apply_url": None, "company_title": "%hirify_global%"}
    assert hirify.parse_item(item).company is None
