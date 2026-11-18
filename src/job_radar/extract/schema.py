"""The strict extraction schema. Pydantic is the contract: an LLM answer that does
not validate against it is retried with the validation error fed back."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Seniority(StrEnum):
    INTERN = "intern"           # Praktikum, Werkstudent, thesis
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"               # lead, staff, principal, head of
    UNKNOWN = "unknown"


class LanguageLevel(StrEnum):
    NONE = "none"               # explicitly not required
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"                   # incl. "native", "Muttersprache"
    UNKNOWN = "unknown"         # not mentioned


LEVEL_ORDER = ["none", "A1", "A2", "B1", "B2", "C1", "C2"]


class RemotePolicy(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"           # fully remote (in Germany or EU)
    UNKNOWN = "unknown"


class Salary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    currency: Literal["EUR", "USD", "CHF", "GBP"] = "EUR"
    period: Literal["year", "month", "hour"] = "year"

    @model_validator(mode="after")
    def _order(self) -> "Salary":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("salary.min must be <= salary.max")
        return self

    def yearly(self) -> tuple[float | None, float | None]:
        factor = {"year": 1, "month": 12, "hour": 1720}[self.period]
        return (self.min * factor if self.min else None, self.max * factor if self.max else None)


class JobExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seniority: Seniority
    skills_required: list[str] = Field(default_factory=list, description="must-have skills, short names")
    skills_nice_to_have: list[str] = Field(default_factory=list)
    german_level: LanguageLevel
    english_level: LanguageLevel
    remote: RemotePolicy
    salary: Salary | None = None
    city: str | None = Field(default=None, description="main work city in Germany, German spelling")
    years_experience_min: int | None = Field(default=None, ge=0, le=30)
    visa_sponsorship: bool | None = None

    @field_validator("skills_required", "skills_nice_to_have")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            s = s.strip()
            if not s:
                continue
            if len(s) > 60:
                raise ValueError(f"skill name too long (>60 chars), use a short name: {s[:40]}...")
            out.append(s)
        return out


def json_schema_for_prompt() -> str:
    import json

    return json.dumps(JobExtraction.model_json_schema(), ensure_ascii=False)
