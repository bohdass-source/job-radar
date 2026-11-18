"""Your profile: built from an uploaded CV or from the guided interview.

Stored in MongoDB (collection `profile`, _id "me") and never written into the
repository. The CV file itself is read once and not copied anywhere.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pymongo.database import Database

from ..extract.llm import LLMClient, LLMInvalidOutput, LLMUnavailable
from ..extract.rules import _language_level
from ..extract.schema import LanguageLevel, Seniority
from ..models import utcnow
from ..skills import all_skills, find_in_text, normalise


class RemotePref(StrEnum):
    ANY = "any"                 # onsite, hybrid or remote
    HYBRID_OR_REMOTE = "hybrid_or_remote"
    REMOTE_ONLY = "remote_only"


class Profile(BaseModel):
    skills: dict[str, int] = Field(default_factory=dict, description="canonical skill -> 1 basics, 2 projects, 3 production")
    skills_other: list[str] = Field(default_factory=list)
    learning: list[str] = Field(default_factory=list, description="skills you are learning now")
    seniority: Seniority = Seniority.JUNIOR
    years_experience: int = 0
    german_level: LanguageLevel = LanguageLevel.UNKNOWN
    english_level: LanguageLevel = LanguageLevel.UNKNOWN
    cities: list[str] = Field(default_factory=list)
    relocate: bool = False
    remote_pref: RemotePref = RemotePref.ANY
    min_salary_year: float | None = None
    exclude_companies: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    summary: str = ""
    source: str = "interview"


class CVExtraction(BaseModel):
    """What the LLM must return when reading a CV."""

    skills: dict[str, int]
    seniority: Seniority
    years_experience: int = Field(ge=0, le=50)
    german_level: LanguageLevel
    english_level: LanguageLevel
    cities: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    summary: str = Field(max_length=1200)


CV_SYSTEM = """You read a CV and return ONE JSON object for job matching. Use only facts in the CV.
- skills: technical skill -> level (1 = basics/coursework, 2 = used in projects, 3 = used in production or 3+ years).
  Prefer these canonical names when they apply: {skills}.
- seniority: intern | junior | mid | senior | lead.  years_experience: professional years, integer.
- german_level, english_level: CEFR A1..C2, "unknown" if not stated.
- cities: cities where the person lives or wants to work.  target_roles: job titles they aim for.
- summary: 3-5 sentences in English describing their profile, for semantic matching.
Return only JSON valid for this schema: {schema}"""


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


def profile_from_text(text: str, llm: LLMClient | None = None) -> Profile:
    import json

    if llm is not None and llm.available:
        try:
            system = CV_SYSTEM.format(skills=", ".join(all_skills()),
                                      schema=json.dumps(CVExtraction.model_json_schema()))
            cv = llm.complete_json(system, text[:12000], CVExtraction)
            known, other = normalise(list(cv.skills))
            skills = {}
            for raw, level in cv.skills.items():
                canon = normalise([raw])[0]
                if canon:
                    skills[canon[0]] = max(skills.get(canon[0], 0), max(1, min(3, level)))
            return Profile(skills=skills, skills_other=other, seniority=cv.seniority,
                           years_experience=cv.years_experience, german_level=cv.german_level,
                           english_level=cv.english_level, cities=cv.cities, target_roles=cv.target_roles,
                           summary=cv.summary, source="upload")
        except (LLMUnavailable, LLMInvalidOutput):
            pass
    # Rules fallback: every skill mentioned counts as "used in projects" (level 2).
    skills = {s: 2 for s in find_in_text(text)}
    years = [int(y) for y in re.findall(r"(\d{1,2})\+?\s*(?:years|jahre)", text, re.I) if int(y) < 40]
    return Profile(
        skills=skills,
        german_level=_language_level(text, r"(deutsch|german)"),
        english_level=_language_level(text, r"(englisch|english)"),
        years_experience=max(years) if years else 0,
        summary=" ".join(text.split()[:200]),
        source="upload",
    )


def save_profile(db: Database, profile: Profile) -> None:
    db.profile.replace_one({"_id": "me"}, {"_id": "me", **profile.model_dump(mode="json"), "updated_at": utcnow()},
                           upsert=True)


def load_profile(db: Database) -> Profile | None:
    doc = db.profile.find_one({"_id": "me"})
    if not doc:
        return None
    doc.pop("_id", None)
    doc.pop("updated_at", None)
    return Profile.model_validate(doc)


def merge_into(profile: Profile, updates: dict[str, Any]) -> Profile:
    data = profile.model_dump()
    for key, value in updates.items():
        if key == "skills":
            data["skills"].update(value)
        elif value is not None:
            data[key] = value
    return Profile.model_validate(data)
