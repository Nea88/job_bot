from types import SimpleNamespace

from job_bot.interview.builder import aggregate
from job_bot.llm.schemas import Extraction, Skill
from job_bot.pipeline.analyze import _requirements
from job_bot.skills import normalize_skill


def test_normalize_skill():
    assert normalize_skill("Postgres") == "postgresql"
    assert normalize_skill(" K8S ") == "kubernetes"
    assert normalize_skill("Some  New Tool") == "some new tool"


def test_requirements_dedup_and_must_wins():
    result = Extraction(
        is_vacancy=True, role="Backend", seniority="middle",
        hard_skills=[Skill(name="Postgres", level="nice"), Skill(name="PostgreSQL", level="must")],
        soft_skills=["Коммуникабельность"],
    )
    reqs = _requirements(result)
    hard = [r for r in reqs if r.kind == "hard"]
    assert len(hard) == 1 and hard[0].skill == "postgresql" and hard[0].level == "must"
    assert any(r.kind == "soft" for r in reqs)


def req(skill, level="must", kind="hard"):
    return SimpleNamespace(skill=skill, level=level, kind=kind)


def test_aggregate_ranks_skills():
    vacancies = [
        SimpleNamespace(role="Backend", seniority="senior", requirements=[req("python"), req("kafka", "nice")]),
        SimpleNamespace(role="Backend", seniority="senior", requirements=[req("python"), req("postgresql")]),
        SimpleNamespace(role="Data", seniority="unknown", requirements=[req("python", "nice"), req("x", kind="soft")]),
    ]
    p = aggregate(vacancies)
    assert p.vacancy_count == 3
    assert p.role == "Backend" and p.seniority == "senior"
    assert [s.skill for s in p.skills] == ["python", "postgresql", "kafka"]
    assert p.skills[0].share == 1.0
    assert round(p.skills[0].must_share, 2) == 0.67


def test_aggregate_empty():
    p = aggregate([])
    assert p.vacancy_count == 0 and p.skills == []
