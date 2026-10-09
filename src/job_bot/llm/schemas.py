from typing import Literal

from pydantic import BaseModel, Field

Seniority = Literal["intern", "junior", "middle", "senior", "lead", "unknown"]


class Skill(BaseModel):
    name: str
    level: Literal["must", "nice"]


class Extraction(BaseModel):
    is_vacancy: bool
    role: str
    seniority: Seniority
    hard_skills: list[Skill]
    soft_skills: list[str]


class InterviewQuestion(BaseModel):
    skill: str
    question: str
    difficulty: Literal["easy", "medium", "hard"]
    answer_points: list[str] = Field(description="Key points of a good answer")


class InterviewPlan(BaseModel):
    questions: list[InterviewQuestion]
