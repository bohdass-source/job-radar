"""Ingest postings into MongoDB: one `postings` doc per (source, source_id), one
`jobs` doc per real-world vacancy with the list of sources that carry it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo.database import Database

from ..models import Posting
from .clean import detect_language, html_to_text
from .dedup import city_key, company_key, is_same_job, make_job_id, title_key
from .freshness import _aware, apply_status

# Which link to show first when a job has several sources: the employer's own page wins.
URL_PRIORITY = ["greenhouse", "lever", "recruitee", "smartrecruiters", "ashby", "personio", "manual",
                "ba_jobsuche", "arbeitnow", "adzuna", "jooble", "linkedin_alerts", "indeed_alerts"]


def _better_description(current: dict[str, Any], new_text: str, new_is_snippet: bool) -> bool:
    cur = current.get("description", "")
    if not cur:
        return bool(new_text)
    if current.get("description_is_snippet") and not new_is_snippet and new_text:
        return True
    return new_is_snippet == current.get("description_is_snippet", False) and len(new_text) > len(cur) * 1.2


def _ref(p: Posting, now: datetime, verifiable: bool) -> dict[str, Any]:
    return {"source": p.source, "source_id": p.source_id, "url": p.url or p.apply_url, "scope": p.scope,
            "first_seen": now, "last_seen": now, "missed_runs": 0, "gone": False, "verifiable": verifiable}


def ingest(db: Database, p: Posting, *, now: datetime, verifiable: bool, freshness_cfg: dict[str, Any]) -> str:
    """Store one posting. Returns 'new_job', 'merged', or 'seen'."""
    text = html_to_text(p.description)
    keys = {"company_key": company_key(p.company), "title_key": title_key(p.title),
            "city_key": city_key(p.city or p.location), "remote": p.remote}

    existing = db.postings.find_one({"source": p.source, "source_id": p.source_id})
    doc = p.model_dump()
    doc.update(description=text, **keys, last_seen=now)
    if existing and not text:
        # A source that skipped the detail call this time must not wipe the text stored earlier.
        doc.pop("description")
        doc.pop("description_is_snippet")
    if existing:
        db.postings.update_one({"_id": existing["_id"]}, {"$set": {**doc, "missed_runs": 0, "gone": False}})
        job = db.jobs.find_one({"job_id": existing["job_id"]})
        outcome = "seen"
    else:
        job = None
        for cand in db.jobs.find({"company_key": keys["company_key"]}) if keys["company_key"] else []:
            if is_same_job(keys, cand):
                job = cand
                break
        outcome = "merged" if job else "new_job"

    if job is None:
        job_id = make_job_id(p.company, p.title, p.city or p.location)
        while db.jobs.find_one({"job_id": job_id}):
            job_id = job_id[:-1] + format((int(job_id[-1], 16) + 1) % 16, "x")
        job = {"job_id": job_id, "title": p.title, "company": p.company, **keys, "city": p.city,
               "location": p.location, "country": p.country, "first_seen": now, "sources": [],
               "description": "", "description_is_snippet": True, "extraction": None}

    refs = job.setdefault("sources", [])
    ref = next((r for r in refs if r["source"] == p.source and r["source_id"] == p.source_id), None)
    if ref:
        ref.update(last_seen=now, missed_runs=0, gone=False, url=p.url or p.apply_url or ref.get("url", ""))
    else:
        refs.append(_ref(p, now, verifiable))

    if _better_description(job, text, p.description_is_snippet):
        job["description"] = text
        job["description_is_snippet"] = p.description_is_snippet
        job["language"] = detect_language(text)
        job["extraction_stale"] = True
    for field in ("company", "city", "location"):
        if not job.get(field) and getattr(p, field):
            job[field] = getattr(p, field)
    if p.remote is not None and job.get("remote") is None:
        job["remote"] = p.remote
    if p.salary_min and not job.get("salary_min"):
        job.update(salary_min=p.salary_min, salary_max=p.salary_max,
                   salary_currency=p.salary_currency, salary_period=p.salary_period)
    posted = _aware(p.posted_at)
    if posted and (not job.get("posted_at") or posted < _aware(job["posted_at"])):
        job["posted_at"] = posted
    # Source-specific facts (coordinates, full/part time, contract, home office...) used by search filters.
    attrs = job.setdefault("attrs", {})
    for key, value in p.extra.items():
        if value is not None and value != [] and key not in ("email_id",):
            attrs.setdefault(key, value)
    if p.extra.get("valid_through"):
        job["valid_through"] = p.extra["valid_through"]
    job["last_seen"] = now
    job["url"] = best_url(refs)

    apply_status(job, freshness_cfg, now)
    db.jobs.replace_one({"job_id": job["job_id"]}, job, upsert=True)
    if not existing:
        db.postings.insert_one({**doc, "job_id": job["job_id"], "first_seen": now, "missed_runs": 0, "gone": False,
                                "verifiable": verifiable})
    return outcome


def best_url(refs: list[dict[str, Any]]) -> str:
    live = [r for r in refs if not r.get("gone") and r.get("url")] or [r for r in refs if r.get("url")]
    live.sort(key=lambda r: URL_PRIORITY.index(r["source"]) if r["source"] in URL_PRIORITY else 99)
    return live[0]["url"] if live else ""


def record_misses(db: Database, source: str, completed_scopes: set[str], seen_ids: set[str], *,
                  now: datetime, missed_to_close: int, freshness_cfg: dict[str, Any]) -> int:
    """For every posting of `source` in a completed scope that this run did not see,
    count a miss; after `missed_to_close` misses the ref is gone. Returns refs closed."""
    closed = 0
    if not completed_scopes:
        return 0
    query = {"source": source, "scope": {"$in": sorted(completed_scopes)}, "gone": {"$ne": True}}
    for post in db.postings.find(query):
        if post["source_id"] in seen_ids:
            continue
        misses = int(post.get("missed_runs", 0)) + 1
        gone = misses >= missed_to_close
        closed += gone
        db.postings.update_one({"_id": post["_id"]}, {"$set": {"missed_runs": misses, "gone": gone}})
        mark_ref(db, post["job_id"], source, post["source_id"], now=now, freshness_cfg=freshness_cfg,
                 missed_runs=misses, gone=gone)
    return closed


def mark_ref(db: Database, job_id: str, source: str, source_id: str, *, now: datetime,
             freshness_cfg: dict[str, Any], **changes: Any) -> None:
    job = db.jobs.find_one({"job_id": job_id})
    if not job:
        return
    for r in job.get("sources", []):
        if r["source"] == source and r["source_id"] == source_id:
            r.update(changes)
    job["url"] = best_url(job.get("sources", []))
    apply_status(job, freshness_cfg, now)
    db.jobs.replace_one({"job_id": job_id}, job)


def recompute_all(db: Database, freshness_cfg: dict[str, Any], now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in db.jobs.find({}):
        before = job.get("status")
        apply_status(job, freshness_cfg, now)
        if job["status"] != before:
            db.jobs.replace_one({"job_id": job["job_id"]}, job)
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return counts
