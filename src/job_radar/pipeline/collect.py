"""`collect` and `refresh`: run approved adapters, ingest, count misses, re-check."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable

from pymongo.database import Database

from ..config import load_yaml
from ..http import BudgetExceeded
from ..models import CollectStats, utcnow
from ..sources.base import SourceAdapter
from ..sources.registry import build, enabled_sources
from .freshness import _aware
from .relevance import is_germany, is_relevant
from .store import ingest, mark_ref, recompute_all, record_misses

log = logging.getLogger(__name__)


def freshness_cfg() -> dict[str, Any]:
    return (load_yaml("sources.yaml") or {}).get("freshness", {})


def run_adapter(db: Database, adapter: SourceAdapter, *, dry_run: bool = False) -> CollectStats:
    cfg = freshness_cfg()
    now = utcnow()
    stats = CollectStats(source=adapter.name)
    seen: set[str] = set()
    try:
        for posting in adapter.fetch():
            stats.fetched += 1
            seen.add(posting.source_id)   # seen counts even if not relevant: it is still listed
            if not is_relevant(posting.title, posting.description):
                continue
            if not is_germany(posting.location, posting.city, posting.country):
                continue
            stats.relevant += 1
            if dry_run:
                print(f"  [{posting.source}] {posting.title} | {posting.company} | {posting.city or posting.location}")
                continue
            outcome = ingest(db, posting, now=now, verifiable=adapter.verifiable, freshness_cfg=cfg)
            if outcome == "new_job":
                stats.new_jobs += 1
                stats.new_postings += 1
            elif outcome == "merged":
                stats.merged_into_existing += 1
                stats.new_postings += 1
    except BudgetExceeded as exc:
        stats.errors.append(str(exc))
    except Exception as exc:  # keep partial results; the scope just doesn't count as complete
        log.exception("%s failed", adapter.name)
        stats.errors.append(f"{type(exc).__name__}: {exc}")
    stats.errors.extend(getattr(adapter, "errors", []) or [])
    stats.completed_scopes = sorted(adapter.completed_scopes)
    stats.requests = adapter.http.requests if adapter.http else 0

    if not dry_run and adapter.verifiable:
        record_misses(db, adapter.name, adapter.completed_scopes, seen, now=now,
                      missed_to_close=int(cfg.get("missed_runs_to_close", 2)), freshness_cfg=cfg)
    if not dry_run:
        db.runs.insert_one({"source": adapter.name, "started_at": now, "finished_at": utcnow(),
                            **stats.model_dump(exclude={"source"})})
    return stats


def collect(db: Database, sources: Iterable[str] | None = None, *, dry_run: bool = False) -> list[CollectStats]:
    names = list(sources) if sources else enabled_sources()
    results = []
    for name in names:
        adapter = build(name)
        log.info("collecting %s", name)
        results.append(run_adapter(db, adapter, dry_run=dry_run))
        if adapter.http:
            adapter.http.close()
    if not dry_run:
        recompute_all(db, freshness_cfg(), utcnow())
    return results


def prune(db: Database, *, dry_run: bool = False) -> dict[str, int]:
    """Re-apply the relevance and Germany rules to stored jobs (after the rules
    changed) and delete the ones that no longer pass. Jobs you track are kept."""
    from .relevance import _geo, _rules

    _rules.cache_clear()
    _geo.cache_clear()
    tracked = {a["job_id"] for a in db.applications.find({}, {"job_id": 1})}
    counts = {"kept": 0, "not_relevant": 0, "not_germany": 0}
    for job in db.jobs.find({}, {"job_id": 1, "title": 1, "description": 1, "location": 1, "city": 1, "country": 1}):
        if job["job_id"] in tracked:
            counts["kept"] += 1
            continue
        reason = None
        if not is_relevant(job.get("title", ""), job.get("description", "")):
            reason = "not_relevant"
        elif not is_germany(job.get("location", ""), job.get("city", ""), job.get("country") or "DE"):
            reason = "not_germany"
        if reason is None:
            counts["kept"] += 1
            continue
        counts[reason] += 1
        if not dry_run:
            db.jobs.delete_one({"job_id": job["job_id"]})
            db.postings.delete_many({"job_id": job["job_id"]})
    return counts


def refresh(db: Database, *, limit: int = 300) -> dict[str, int]:
    """Re-check open jobs through detail endpoints (where a source has one), then
    recompute every status so TTLs and max-age rules apply even without new runs."""
    cfg = freshness_cfg()
    now = utcnow()
    cutoff = now - timedelta(hours=int(cfg.get("recheck_after_hours", 24)))
    adapters: dict[str, SourceAdapter | None] = {}
    counts = {"checked": 0, "closed": 0, "confirmed": 0, "unknown": 0}
    for job in db.jobs.find({"status": "open"}).limit(limit * 3):
        if counts["checked"] >= limit:
            break
        lv = _aware(job.get("last_verified"))
        if lv and lv > cutoff:
            continue
        for ref in job.get("sources", []):
            if ref.get("gone") or not ref.get("verifiable", True):
                continue
            name = ref["source"]
            if name not in adapters:
                try:
                    adapters[name] = build(name)
                except Exception:      # not approved / unknown: cannot re-check via this source
                    adapters[name] = None
            adapter = adapters[name]
            if adapter is None or type(adapter).check is SourceAdapter.check:
                continue
            posting = db.postings.find_one({"source": name, "source_id": ref["source_id"]})
            if not posting:
                continue
            try:
                result = adapter.check(posting)
            except BudgetExceeded:
                adapters[name] = None
                continue
            counts["checked"] += 1
            if result == "closed":
                counts["closed"] += 1
                db.postings.update_one({"_id": posting["_id"]}, {"$set": {"gone": True}})
                mark_ref(db, job["job_id"], name, ref["source_id"], now=now, freshness_cfg=cfg, gone=True)
            elif result == "open":
                counts["confirmed"] += 1
                mark_ref(db, job["job_id"], name, ref["source_id"], now=now, freshness_cfg=cfg, last_seen=now)
            else:
                counts["unknown"] += 1
    for adapter in adapters.values():
        if adapter and adapter.http:
            adapter.http.close()
    counts.update({f"status_{k}": v for k, v in recompute_all(db, cfg, now).items()})
    return counts
