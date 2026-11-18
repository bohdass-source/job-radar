"""Sort live jobs into three buckets against your profile.

  apply    — подходит: required skills covered, no blockers. Можно подаваться.
  stretch  — доработать: close, but something is missing. Apply and say honestly
             what you are still learning, or learn it first. Missing skills listed.
  reject   — не подходит: a hard blocker (language gap, location, level, salary,
             too few required skills). Filtered out, reasons kept for the report.

Closed and expired jobs never reach this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pymongo.database import Database

from ..extract.rules import extract_rules
from ..extract.schema import LEVEL_ORDER
from ..pipeline.dedup import city_key
from ..search.embed import Embedder
from .profile import Profile, RemotePref

Bucket = Literal["apply", "stretch", "reject"]
SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead"]

APPLY_COVERAGE = 0.8
REJECT_COVERAGE = 0.4


@dataclass
class MatchResult:
    job_id: str
    bucket: Bucket
    score: float
    coverage: float
    matched: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_nice: list[str] = field(default_factory=list)
    learning_hits: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)   # -> reject
    gaps: list[str] = field(default_factory=list)       # -> stretch
    notes: list[str] = field(default_factory=list)      # informational


def _lvl(level: str | None) -> int | None:
    return LEVEL_ORDER.index(level) if level in LEVEL_ORDER else None


def _language(name: str, required: str | None, have: str, job_lang: str | None, res: MatchResult) -> None:
    need = _lvl(required)
    inferred = False
    if need is None and name == "Deutsch" and job_lang == "de":
        need, inferred = LEVEL_ORDER.index("B2"), True   # German ad, level unstated: assume working German
    if need is None and name == "Englisch" and job_lang == "en":
        need, inferred = LEVEL_ORDER.index("B2"), True
    if need is None or need == 0:
        return
    got = _lvl(have)
    if got is None:
        res.notes.append(f"{name}: требуется {LEVEL_ORDER[need]}, ваш уровень не указан в профиле")
        return
    suffix = " (не указан явно, объявление на этом языке)" if inferred else ""
    if got >= need:
        return
    if need - got >= 2 and not inferred:
        res.blockers.append(f"{name}: требуется {LEVEL_ORDER[need]}, у вас {have}")
    else:
        res.gaps.append(f"{name}: нужно {LEVEL_ORDER[need]}{suffix}, у вас {have}")


def evaluate(job: dict[str, Any], profile: Profile, similarity: float | None = None,
             lenient_skills: bool = False) -> MatchResult:
    """lenient_skills: missing skills never reject (discovery mode — the user is
    choosing what to learn, so a skill gap is a learning item, not a blocker)."""
    ext = job.get("extraction")
    if not ext:
        from ..extract.extractor import to_document

        ext = to_document(extract_rules(job.get("title", ""), job.get("description", ""), job.get("city")))
    have = {s for s, lvl in profile.skills.items() if lvl >= 1}
    required = ext.get("skills_required", [])
    nice = ext.get("skills_nice_to_have", [])
    matched = [s for s in required if s in have]
    missing = [s for s in required if s not in have]
    coverage = len(matched) / len(required) if required else 0.5
    nice_cov = sum(s in have for s in nice) / len(nice) if nice else 0.0
    res = MatchResult(job["job_id"], "apply", 0.0, round(coverage, 2), matched=matched, missing_required=missing,
                      missing_nice=[s for s in nice if s not in have],
                      learning_hits=[s for s in missing if s in profile.learning])

    if not required:
        # No evidence either way: never "apply" blindly, ask for a manual look.
        res.gaps.append("навыки в объявлении не распознаны — проверьте вручную")

    # Languages
    _language("Deutsch", ext.get("german_level"), profile.german_level, job.get("language"), res)
    _language("Englisch", ext.get("english_level"), profile.english_level, job.get("language"), res)

    # Location / remote
    remote = ext.get("remote") or ("remote" if job.get("remote") else "unknown")
    jcity = city_key(ext.get("city") or job.get("city") or "")
    my_cities = {city_key(c) for c in profile.cities}
    if remote != "remote":
        if profile.remote_pref == RemotePref.REMOTE_ONLY:
            if remote == "unknown":
                res.gaps.append("формат работы не указан, вам нужен только remote — уточните")
            else:
                res.blockers.append(f"формат {remote}, вам нужен только remote")
        elif profile.remote_pref == RemotePref.HYBRID_OR_REMOTE and remote == "onsite":
            res.blockers.append("только офис, вам нужен hybrid/remote")
        if jcity and my_cities and jcity not in my_cities and not res.blockers:
            if profile.relocate:
                res.notes.append(f"город {ext.get('city') or job.get('city')} — потребуется переезд")
            elif remote != "unknown" or profile.remote_pref != RemotePref.ANY:
                res.blockers.append(f"город {ext.get('city') or job.get('city')} не в вашем списке")
            else:
                res.gaps.append(f"город {ext.get('city') or job.get('city')} не в вашем списке, формат не указан")

    # Seniority and experience
    js, ms = ext.get("seniority"), str(profile.seniority)
    if js in SENIORITY_ORDER and ms in SENIORITY_ORDER:
        diff = SENIORITY_ORDER.index(js) - SENIORITY_ORDER.index(ms)
        if diff >= 2:
            res.blockers.append(f"уровень {js}, ваш {ms}")
        elif diff == 1:
            res.gaps.append(f"уровень {js} на ступень выше вашего ({ms})")
        elif js == "intern" and SENIORITY_ORDER.index(ms) >= 2:
            res.blockers.append("стажировка/Werkstudent ниже вашего уровня")
    yrs = ext.get("years_experience_min")
    if yrs is not None:
        if yrs > profile.years_experience + 2:
            res.blockers.append(f"нужно {yrs}+ лет опыта, у вас {profile.years_experience}")
        elif yrs > profile.years_experience:
            res.gaps.append(f"нужно {yrs}+ лет опыта, у вас {profile.years_experience}")

    # Salary
    sal = ext.get("salary") or {}
    if profile.min_salary_year and sal.get("max"):
        factor = {"year": 1, "month": 12, "hour": 1720}.get(sal.get("period", "year"), 1)
        if sal["max"] * factor < profile.min_salary_year:
            res.blockers.append(f"зарплата до {int(sal['max'] * factor)} EUR/год ниже вашего минимума")

    # Company
    if any(c.lower() in (job.get("company") or "").lower() for c in profile.exclude_companies if c):
        res.blockers.append("компания в вашем списке исключений")

    # Skills
    if required and coverage < REJECT_COVERAGE and not lenient_skills:
        res.blockers.append(f"есть только {len(matched)} из {len(required)} обязательных навыков")
    elif required and coverage < APPLY_COVERAGE:  # also reached in lenient mode below REJECT_COVERAGE
        res.gaps.append(f"покрыто {len(matched)} из {len(required)} обязательных навыков")

    if job.get("status") == "unverified":
        res.notes.append("не проверено автоматически (письмо/ручная страница) — откройте ссылку перед подачей")

    sim = similarity if similarity is not None else 0.0
    w_sim = 0.25 if similarity is not None else 0.0
    res.score = round((0.6 * coverage + 0.15 * nice_cov + w_sim * max(0.0, sim)) / (0.75 + w_sim), 3)
    res.bucket = "reject" if res.blockers else ("stretch" if res.gaps else "apply")
    return res


def application_note(res: MatchResult) -> str:
    """One honest sentence for the cover letter of a 'stretch' job."""
    if not res.missing_required:
        return ""
    learning = [s for s in res.missing_required if s in res.learning_hits]
    rest = [s for s in res.missing_required if s not in learning]
    parts = []
    if learning:
        parts.append(f"I am currently learning {', '.join(learning)}")
    if rest:
        parts.append(f"I have not yet worked with {', '.join(rest)} in production and would ramp up quickly")
    return "; ".join(parts) + "."


def match_all(db: Database, profile: Profile, embedder: Embedder | None = None,
              include_tracked: bool = False) -> list[tuple[dict[str, Any], MatchResult]]:
    jobs = list(db.jobs.find({"status": {"$in": ["open", "unverified"]}}))
    tracked = {a["job_id"]: a["status"] for a in db.applications.find({}, {"job_id": 1, "status": 1})}
    if not include_tracked:
        jobs = [j for j in jobs if tracked.get(j["job_id"]) in (None, "viewed", "planned")]
    sims: dict[str, float] = {}
    if embedder is not None and jobs:
        text = profile.summary or " ".join(profile.target_roles + list(profile.skills))
        pv = embedder.encode([text], "query")[0]
        jv = embedder.encode([f"{j.get('title', '')}. {j.get('description', '')[:1500]}" for j in jobs], "passage")
        sims = {j["job_id"]: float(s) for j, s in zip(jobs, jv @ pv / (np.linalg.norm(pv) or 1.0))}
    out = [(j, evaluate(j, profile, sims.get(j["job_id"]))) for j in jobs]
    rank = {"apply": 0, "stretch": 1, "reject": 2}
    out.sort(key=lambda t: (rank[t[1].bucket], -t[1].score))
    return out
