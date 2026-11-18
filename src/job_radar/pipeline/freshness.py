"""Is a job still open? Decided only from evidence we are allowed to gather.

Evidence, strongest first:
1. A detail endpoint answered 404 (ATS / BA)            -> that source ref is gone.
2. A *completed* listing no longer contains the job     -> a miss; N misses -> gone.
3. A verifiable source listed it in the last run        -> open, last_verified = now.
4. Only mail alerts / manual pages know the job         -> unverified, expires after a TTL
   (or at the page's own schema.org `validThrough`).
A job with at least one live verifiable ref is open; with only dead verifiable
refs it is closed. Closed and expired jobs are excluded everywhere downstream.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import JobStatus

ATS_SOURCES = {"greenhouse", "lever", "recruitee", "smartrecruiters", "ashby", "personio"}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_status(job: dict[str, Any], cfg: dict[str, Any], now: datetime) -> tuple[JobStatus, str]:
    refs = job.get("sources", [])
    verifiable = [r for r in refs if r.get("verifiable", True)]
    live = [r for r in verifiable if not r.get("gone")]
    first_seen = _aware(job.get("first_seen")) or now
    max_age = timedelta(days=int(cfg.get("max_age_days", 90)))

    if live:
        has_ats = any(r["source"] in ATS_SOURCES for r in live)
        # Aggregators sometimes keep listing stale ads; an ATS feed is authoritative.
        if not has_ats and now - first_seen > max_age:
            return JobStatus.EXPIRED, f"older than {max_age.days} days and not confirmed by a company feed"
        return JobStatus.OPEN, "listed by " + ", ".join(sorted({r["source"] for r in live}))
    if verifiable:
        return JobStatus.CLOSED, "no longer listed by " + ", ".join(sorted({r["source"] for r in verifiable}))

    valid_through = job.get("valid_through")
    if valid_through:
        vt = _aware(datetime.fromisoformat(str(valid_through).replace("Z", "+00:00")))
        if vt and vt < now:
            return JobStatus.EXPIRED, f"validThrough {vt.date()} has passed"
    last_seen = max((_aware(r.get("last_seen")) or first_seen for r in refs), default=first_seen)
    ttl = timedelta(days=int(cfg.get("unverified_ttl_days", 21)))
    if now - last_seen > ttl:
        return JobStatus.EXPIRED, f"only known from alerts/manual pages and not seen for {ttl.days} days"
    if now - first_seen > max_age:
        return JobStatus.EXPIRED, f"older than {max_age.days} days"
    return JobStatus.UNVERIFIED, "only known from alerts/manual pages; cannot be re-checked automatically"


def apply_status(job: dict[str, Any], cfg: dict[str, Any], now: datetime) -> dict[str, Any]:
    status, reason = compute_status(job, cfg, now)
    if job.get("status") != status:
        job.setdefault("status_history", []).append({"at": now, "status": str(status), "reason": reason})
    job["status"] = str(status)
    job["status_reason"] = reason
    live_verifiable = [r for r in job.get("sources", []) if r.get("verifiable", True) and not r.get("gone")]
    if live_verifiable:
        job["last_verified"] = max(_aware(r["last_seen"]) for r in live_verifiable)
    return job
