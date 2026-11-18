"""Mode 2 — a questionnaire you fill in freely; the AI turns it into a profile and filters.

`template()` writes a Markdown file with one heading per question (the same
questions as the interview, from MongoDB, plus the search preferences). You
answer in your own words, in any language. `process()` sends the answers to
the LLM, which must return the strict `QuestionnaireResult` schema (validated,
retried on invalid JSON). Without an LLM a rules parser reads each section.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field
from pymongo.database import Database

from ..extract.llm import LLMClient, LLMInvalidOutput, LLMUnavailable
from ..extract.rules import _language_level
from ..extract.schema import LanguageLevel, Seniority
from ..match.interview import seed_questions
from ..match.profile import Profile, RemotePref
from ..skills import all_skills, find_in_text, normalise
from .filters import SearchFilters
from ..i18n import question_text, t

EXTRA_QUESTIONS = ["radius_km", "workplace", "salary", "restrictions"]   # wording: i18n keys q.<id>


class QuestionnaireResult(BaseModel):
    skills: dict[str, int] = Field(description="skill -> 1 basics, 2 projects, 3 production")
    learning: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    seniority: Seniority
    years_experience: int = Field(ge=0, le=50)
    german_level: LanguageLevel
    english_level: LanguageLevel
    cities: list[str] = Field(default_factory=list)
    radius_km: int = Field(default=0, ge=0, le=800)
    workplace: list[str] = Field(default_factory=list, description="subset of remote, hybrid, onsite; empty = any")
    salary_min: int | None = None
    salary_max: int | None = None
    no_agency: bool = False
    fulltime: bool | None = None
    permanent_only: bool = False
    exclude_companies: list[str] = Field(default_factory=list)
    other_restrictions: list[str] = Field(default_factory=list, description="anything else, short phrases")
    summary: str = Field(max_length=1200, description="3-5 sentences in English about the person")


SYSTEM = """You turn a job seeker's free-text questionnaire into ONE JSON object for job matching in Germany.
Use only what the answers say; leave unknowns at their defaults ("unknown" for levels).
skills: canonical names where possible ({skills}); level 1 = basics/courses, 2 = projects, 3 = production or 3+ years.
workplace: subset of ["remote","hybrid","onsite"]. salary in EUR per year. Answers may be Russian, German or English.
Return only JSON valid for this schema: {schema}"""


def template(db: Database, path: Path) -> Path:
    if not db.questions.count_documents({}):
        seed_questions(db)
    lines = [t("q.title"), "", t("q.intro"), t("q.private"), ""]
    for q in db.questions.find({}).sort("order", 1):
        if q["type"] in ("skill_level", "market_gaps"):
            continue
        text = question_text(q)
        if q["type"] == "skills":
            text = text.rstrip(":") + t("q.skills_suffix")
        lines += [f"## {q['qid']}: {text}", "", ""]
    for qid in EXTRA_QUESTIONS:
        lines += [f"## {qid}: {t('q.' + qid)}", "", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        out[head.split(":")[0].strip()] = body.strip()
    return out


def _rules(text: str) -> QuestionnaireResult:
    sec = _sections(text)
    skills = {}
    for key, body in sec.items():
        if key.startswith("skills"):
            for line in re.split(r"[,\n;]", body):
                for s in find_in_text(line):
                    lvl = 3 if re.search(r"прод|production|3|эксперт|expert", line, re.I) else \
                        1 if re.search(r"основ|basic|курс|1", line, re.I) else 2
                    skills[s] = max(skills.get(s, 0), lvl)
    nums = lambda s: [int(x.replace(" ", "").replace(".", "")) for x in re.findall(r"\d[\d .]*\d|\d", s or "")]
    salary = [n for n in nums(sec.get("salary", "")) if n >= 1000]
    wp_text = sec.get("workplace", "").lower()
    workplace = [w for w, pat in (("remote", r"удал|remote"), ("hybrid", r"гибр|hybrid"), ("onsite", r"офис|office|vor ort"))
                 if re.search(pat, wp_text)]
    restr = sec.get("restrictions", "").lower()
    level = lambda key: next((lv for lv in ["C2", "C1", "B2", "B1", "A2", "A1"] if lv in sec.get(key, "").upper()), None)
    return QuestionnaireResult(
        skills=skills,
        learning=find_in_text(sec.get("learning", "")),
        target_roles=[x.strip() for x in re.split(r"[,\n]", sec.get("target_roles", "")) if x.strip()],
        seniority=next((s for s in ["intern", "junior", "mid", "senior", "lead"] if s in sec.get("seniority", "").lower()), "junior"),
        years_experience=(nums(sec.get("years_experience", "")) or [0])[0] if nums(sec.get("years_experience", "")) else 0,
        german_level=level("german_level") or _language_level(sec.get("german_level", "") + " deutsch", "deutsch"),
        english_level=level("english_level") or _language_level(sec.get("english_level", "") + " english", "english"),
        cities=[x.strip() for x in re.split(r"[,\n]", sec.get("cities", "")) if x.strip()],
        radius_km=(nums(sec.get("radius_km", "")) or [0])[0],
        workplace=workplace,
        salary_min=salary[0] if salary else None,
        salary_max=salary[1] if len(salary) > 1 else None,
        no_agency=bool(re.search(r"без (кадров|агент)|no agenc|keine (zeitarbeit|personaldienst)", restr)),
        fulltime=False if re.search(r"частичн|part[- ]time|teilzeit", restr) else
        (True if re.search(r"полн|full[- ]time|vollzeit", restr) else None),
        permanent_only=bool(re.search(r"бессроч|unbefristet|permanent", restr)),
        exclude_companies=[x.strip() for x in re.split(r"[,\n]", sec.get("exclude_companies", "")) if x.strip()],
        other_restrictions=[restr] if restr else [],
        summary=" ".join(sec.get("experience_story", "").split())[:1200],
    )


def process(text: str, llm: LLMClient | None = None) -> tuple[Profile, SearchFilters, QuestionnaireResult, str]:
    result, method = None, "rules"
    if llm is not None and llm.available:
        try:
            system = SYSTEM.format(skills=", ".join(all_skills()), schema=json.dumps(QuestionnaireResult.model_json_schema()))
            result, method = llm.complete_json(system, text[:15000], QuestionnaireResult), "llm"
        except (LLMUnavailable, LLMInvalidOutput):
            result = None
    result = result or _rules(text)
    skills: dict[str, int] = {}
    for raw, lvl in result.skills.items():
        known, _ = normalise([raw])
        if known:
            skills[known[0]] = max(skills.get(known[0], 0), max(1, min(3, int(lvl))))
    wp = set(result.workplace)
    remote_pref = RemotePref.REMOTE_ONLY if wp == {"remote"} else \
        RemotePref.HYBRID_OR_REMOTE if wp and "onsite" not in wp else RemotePref.ANY
    profile = Profile(skills=skills, learning=normalise(result.learning)[0], target_roles=result.target_roles,
                      seniority=result.seniority, years_experience=result.years_experience,
                      german_level=result.german_level, english_level=result.english_level, cities=result.cities,
                      relocate=False, remote_pref=remote_pref, min_salary_year=result.salary_min,
                      exclude_companies=result.exclude_companies, summary=result.summary, source=f"questionnaire-{method}")
    filters = SearchFilters(
        cities=result.cities, radius_km=result.radius_km,
        workplace=[w for w in result.workplace if w in ("remote", "hybrid", "onsite")],
        salary_min=result.salary_min, salary_max=result.salary_max,
        german_max=None if result.german_level == "unknown" else str(result.german_level),
        english_max=None if result.english_level == "unknown" else str(result.english_level),
        no_agency=result.no_agency, fulltime=result.fulltime, permanent_only=result.permanent_only,
        exclude_companies=result.exclude_companies,
    )
    return profile, filters, result, method
