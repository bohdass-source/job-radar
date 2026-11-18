"""MongoDB access. Tests swap in mongomock via set_db()."""

from __future__ import annotations

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from .config import settings

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        s = settings()
        client = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        _db = client[s.mongo_db]
        ensure_indexes(_db)
    return _db


def set_db(db: Database) -> None:
    global _db
    _db = db
    ensure_indexes(db)


def ensure_indexes(db: Database) -> None:
    # One document per (source, source_id): re-running collect can never duplicate.
    db.postings.create_index([("source", ASCENDING), ("source_id", ASCENDING)], unique=True)
    db.postings.create_index("job_id")
    db.jobs.create_index("job_id", unique=True)
    db.jobs.create_index("company_key")
    db.jobs.create_index("status")
    db.applications.create_index("job_id", unique=True)
    db.questions.create_index("qid", unique=True)
    db.runs.create_index([("source", ASCENDING), ("started_at", ASCENDING)])
