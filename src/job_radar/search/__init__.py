"""Index building and query helpers bound to the database."""

from __future__ import annotations

from pathlib import Path

from pymongo.database import Database

from ..config import PRIVATE_DIR
from .embed import DEFAULT_MODEL, Embedder, get_embedder
from .index import Hit, VectorIndex

DEFAULT_CHUNKER = "section"
LIVE = ["open", "unverified"]


def index_dir(chunker: str = DEFAULT_CHUNKER, model: str = DEFAULT_MODEL) -> Path:
    return PRIVATE_DIR / "index" / f"{chunker}-{model}"


def build_index(db: Database, chunker: str = DEFAULT_CHUNKER, model: str = DEFAULT_MODEL,
                embedder: Embedder | None = None) -> VectorIndex:
    embedder = embedder or get_embedder(model)
    # Index everything with text; liveness is filtered at query time from the DB,
    # so a job that closes after indexing is never returned.
    jobs = db.jobs.find({"description": {"$ne": ""}}, {"job_id": 1, "title": 1, "company": 1, "description": 1})
    idx = VectorIndex.build(jobs, embedder, chunker)
    idx.save(index_dir(chunker, model))
    return idx


def live_job_ids(db: Database) -> set[str]:
    return {d["job_id"] for d in db.jobs.find({"status": {"$in": LIVE}}, {"job_id": 1})}


def search(db: Database, query: str, k: int = 10, chunker: str = DEFAULT_CHUNKER, model: str = DEFAULT_MODEL,
           embedder: Embedder | None = None, index: VectorIndex | None = None) -> list[Hit]:
    embedder = embedder or get_embedder(model)
    index = index or VectorIndex.load(index_dir(chunker, model))
    qv = embedder.encode([query], "query")[0]
    return index.search(qv, k=k, allowed_jobs=live_job_ids(db))
