"""Aggregate statistics. These numbers are the only thing that may leave data/private."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from pymongo.database import Database

from ..pipeline.dedup import display_city
from ..skills import category_of

PRIVATE_ONLY_SOURCES = {"linkedin_alerts", "indeed_alerts", "manual"}
LIVE = ("open", "unverified")


def _pct(counter: Counter[str], total: int, top: int | None = None) -> list[tuple[str, int, float]]:
    items = counter.most_common(top)
    return [(k, v, round(100 * v / total, 1) if total else 0.0) for k, v in items]


def source_table(db: Database) -> list[dict[str, Any]]:
    """Per source: jobs it carries, jobs only it carries (unique contribution), and open ones."""
    carried: Counter[str] = Counter()
    exclusive: Counter[str] = Counter()
    open_: Counter[str] = Counter()
    for job in db.jobs.find({}, {"sources.source": 1, "status": 1}):
        names = {r["source"] for r in job.get("sources", [])}
        for n in names:
            carried[n] += 1
            if job.get("status") in LIVE:
                open_[n] += 1
        if len(names) == 1:
            exclusive[next(iter(names))] += 1
    runs = {r["_id"]: r for r in db.runs.aggregate([
        {"$sort": {"started_at": -1}},
        {"$group": {"_id": "$source", "last_run": {"$first": "$started_at"}, "errors": {"$first": "$errors"}}},
    ])}
    return [{"source": s, "jobs": carried[s], "unique": exclusive[s], "live": open_[s],
             "last_run": runs.get(s, {}).get("last_run")} for s in sorted(carried, key=lambda s: -carried[s])]


def market_stats(db: Database, include_private_sources: bool = False) -> dict[str, Any]:
    jobs = []
    for job in db.jobs.find({"status": {"$in": list(LIVE)}}):
        names = {r["source"] for r in job.get("sources", [])}
        if not include_private_sources and names <= PRIVATE_ONLY_SOURCES:
            continue
        jobs.append(job)
    extracted = [j for j in jobs if j.get("extraction")]
    n = len(extracted)
    req: Counter[str] = Counter()
    nice: Counter[str] = Counter()
    cat: Counter[str] = Counter()
    german: Counter[str] = Counter()
    english: Counter[str] = Counter()
    remote: Counter[str] = Counter()
    seniority: Counter[str] = Counter()
    cities: Counter[str] = Counter()
    lang: Counter[str] = Counter()
    sal_lo: list[float] = []
    sal_hi: list[float] = []
    for j in extracted:
        e = j["extraction"]
        req.update(set(e.get("skills_required", [])))
        nice.update(set(e.get("skills_nice_to_have", [])))
        cat.update({category_of(s) for s in e.get("skills_required", [])})
        german[e.get("german_level", "unknown")] += 1
        english[e.get("english_level", "unknown")] += 1
        remote[e.get("remote", "unknown")] += 1
        seniority[e.get("seniority", "unknown")] += 1
        city = display_city(e.get("city") or j.get("city") or "")
        if city:
            cities[city] += 1
        lang[j.get("language", "unknown")] += 1
        s = e.get("salary") or {}
        factor = {"year": 1, "month": 12, "hour": 1720}.get(s.get("period", "year"), 1)
        if s.get("min"):
            sal_lo.append(s["min"] * factor)
        if s.get("max"):
            sal_hi.append(s["max"] * factor)
    by_status = Counter(d.get("status", "unknown") for d in db.jobs.find({}, {"status": 1}))
    return {
        "live_jobs": len(jobs),
        "extracted": n,
        "snippet_share": round(100 * sum(1 for j in extracted if j.get("description_is_snippet")) / n, 1) if n else 0,
        "status": dict(by_status),
        "skills_required": _pct(req, n, 30),
        "skills_nice": _pct(nice, n, 15),
        "skill_categories": _pct(cat, n),
        "german": _pct(german, n),
        "english": _pct(english, n),
        "remote": _pct(remote, n),
        "seniority": _pct(seniority, n),
        "cities": _pct(cities, n, 12),
        "ad_language": _pct(lang, n),
        "salary": {"n": len(sal_lo), "median_min": round(median(sal_lo)) if sal_lo else None,
                   "median_max": round(median(sal_hi)) if sal_hi else None},
        "total_jobs_ever": db.jobs.count_documents({}),
        "postings": db.postings.count_documents({}),
    }
