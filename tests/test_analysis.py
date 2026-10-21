from types import SimpleNamespace

from job_bot.interview.builder import _pick_tasks, aggregate
from job_bot.llm.schemas import Extraction, Skill
from job_bot.pipeline.analyze import _requirements, _tasks
from job_bot.skills import normalize_skill


def test_normalize_skill():
    assert normalize_skill("Postgres") == "postgresql"
    assert normalize_skill(" K8S ") == "kubernetes"
    assert normalize_skill("Some  New Tool") == "some new tool"


def test_requirements_dedup_and_must_wins():
    result = Extraction(
        is_vacancy=True, role="Backend", seniority="middle", domain="", tasks=[],
        hard_skills=[Skill(name="Postgres", level="nice"), Skill(name="PostgreSQL", level="must")],
        soft_skills=["Коммуникабельность"],
    )
    reqs = _requirements(result)
    hard = [r for r in reqs if r.kind == "hard"]
    assert len(hard) == 1 and hard[0].skill == "postgresql" and hard[0].level == "must"
    assert any(r.kind == "soft" for r in reqs)


def req(skill, level="must", kind="hard"):
    return SimpleNamespace(skill=skill, level=level, kind=kind)


def vac(role, seniority, requirements, domain=None, tasks=None):
    return SimpleNamespace(role=role, seniority=seniority, requirements=requirements, domain=domain, tasks=tasks)


def test_aggregate_ranks_skills():
    vacancies = [
        vac("Backend", "senior", [req("python"), req("kafka", "nice")], domain="Финтех", tasks=["платежи на Kafka"]),
        vac("Backend", "senior", [req("python"), req("postgresql")], domain="финтех"),
        vac("Data", "unknown", [req("python", "nice"), req("x", kind="soft")], domain="ритейл"),
    ]
    p = aggregate(vacancies)
    assert p.vacancy_count == 3
    assert p.role == "Backend" and p.seniority == "senior"
    assert [s.skill for s in p.skills] == ["python", "postgresql", "kafka"]
    assert p.skills[0].share == 1.0
    assert round(p.skills[0].must_share, 2) == 0.67
    assert p.domains == ["финтех", "ритейл"]
    assert p.tasks == ["платежи на Kafka"]


def test_aggregate_empty():
    p = aggregate([])
    assert p.vacancy_count == 0 and p.skills == [] and p.tasks == []


def test_tasks_cleanup():
    assert _tasks(["  Писать  сервисы на Go.", "писать сервисы на go", "", *map(str, range(10))]) == [
        "Писать сервисы на Go", "0", "1", "2", "3", "4"
    ]


def test_pick_tasks_round_robin():
    long = [f"a{i}" for i in range(6)]
    assert _pick_tasks([long, ["b0", "A0"], ["c0"]], top=5) == ["a0", "b0", "c0", "a1", "a2"]
