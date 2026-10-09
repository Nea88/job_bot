from types import SimpleNamespace

from job_bot.matching import matches, query_for


def make_filter(**kw):
    base = dict(
        keywords_any=["python"], keywords_all=[], exclude=[], city=None, hh_area_id=None,
        schedule=None, experience=None, salary_min=None, currency="RUR", sources=["hh", "habr", "telegram"],
    )
    return SimpleNamespace(**{**base, **kw})


def make_vacancy(**kw):
    base = dict(
        source="hh", title="Python-разработчик", description="Django, PostgreSQL", area="Москва",
        schedule="remote", experience="between1And3", salary_from=200_000, salary_to=300_000, currency="RUR",
    )
    return SimpleNamespace(**{**base, **kw})


def test_any_keyword_whole_word():
    assert matches(make_filter(keywords_any=["python", "go"]), make_vacancy())
    assert not matches(make_filter(keywords_any=["java"]), make_vacancy(description="JavaScript"))


def test_prefix_keyword():
    v = make_vacancy(title="Ищем разработчика", description="")
    assert not matches(make_filter(keywords_any=["разработчик"]), v)
    assert matches(make_filter(keywords_any=["разработ*"]), v)


def test_special_chars_keyword():
    assert matches(make_filter(keywords_any=["c++"]), make_vacancy(title="C++ developer"))


def test_all_and_exclude():
    v = make_vacancy()
    assert matches(make_filter(keywords_all=["django", "postgresql"]), v)
    assert not matches(make_filter(keywords_all=["django", "kafka"]), v)
    assert not matches(make_filter(exclude=["django"]), v)


def test_sources():
    assert not matches(make_filter(sources=["habr"]), make_vacancy())


def test_schedule():
    assert matches(make_filter(schedule="remote"), make_vacancy(schedule="remote,hybrid"))
    assert not matches(make_filter(schedule="remote"), make_vacancy(schedule="office"))
    assert not matches(make_filter(schedule="remote"), make_vacancy(schedule=None))
    assert matches(make_filter(schedule="office"), make_vacancy(schedule=None))


def test_city_ignored_for_remote():
    assert matches(make_filter(city="Санкт-Петербург"), make_vacancy(schedule="remote"))
    assert not matches(make_filter(city="Санкт-Петербург"), make_vacancy(schedule="office"))


def test_salary():
    assert matches(make_filter(salary_min=250_000), make_vacancy())
    assert not matches(make_filter(salary_min=400_000), make_vacancy())
    # no salary info or another currency: can't compare, keep it
    assert matches(make_filter(salary_min=400_000), make_vacancy(salary_from=None, salary_to=None))
    assert matches(make_filter(salary_min=400_000), make_vacancy(currency="USD", salary_to=5000))


def test_experience():
    assert not matches(make_filter(experience="moreThan6"), make_vacancy())
    assert matches(make_filter(experience="moreThan6"), make_vacancy(experience=None))


def test_same_filters_share_query():
    assert query_for(make_filter()) == query_for(make_filter())
    assert query_for(make_filter()).key != query_for(make_filter(salary_min=1)).key


def test_parse_salary():
    from job_bot.bot.handlers.filters import parse_salary

    assert parse_salary("300") == 300_000
    assert parse_salary("300к") == 300_000
    assert parse_salary("250k") == 250_000
    assert parse_salary("250000") == 250_000
    assert parse_salary("много") is None
