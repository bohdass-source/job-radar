"""Application tracker: what you have viewed, plan to apply to, applied to, and the outcome."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from .models import utcnow

STATUSES = ["viewed", "planned", "applied", "interview", "offer", "rejected", "withdrawn"]


def set_status(db: Database, job_id: str, status: str, note: str = "") -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    job = db.jobs.find_one({"job_id": job_id}, {"title": 1, "company": 1, "url": 1})
    if not job:
        raise KeyError(f"no job {job_id}")
    now = utcnow()
    existing = db.applications.find_one({"job_id": job_id})
    # Never downgrade an application to "viewed" just because you opened it again.
    if existing and status == "viewed" and existing["status"] != "viewed":
        return existing
    entry = {"at": now, "status": status, "note": note}
    db.applications.update_one(
        {"job_id": job_id},
        {"$set": {"status": status, "updated_at": now, "title": job.get("title"), "company": job.get("company"),
                  "url": job.get("url")},
         "$setOnInsert": {"created_at": now},
         "$push": {"history": entry}},
        upsert=True,
    )
    return db.applications.find_one({"job_id": job_id})


def list_applications(db: Database, status: str | None = None) -> list[dict[str, Any]]:
    query = {"status": status} if status else {}
    apps = list(db.applications.find(query).sort("updated_at", -1))
    live = {j["job_id"]: j.get("status") for j in db.jobs.find({"job_id": {"$in": [a["job_id"] for a in apps]}},
                                                              {"job_id": 1, "status": 1})}
    for a in apps:
        a["job_status"] = live.get(a["job_id"], "unknown")
    return apps
