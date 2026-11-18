"""Vectors in a file, cosine similarity in numpy. No vector database.

Layout of an index directory (under data/private/index/<chunker>-<model>/):
  vectors.npy   float32 [n_chunks, dim], rows L2-normalised -> cosine = dot product
  chunks.jsonl  one {"chunk_id", "job_id", "text"} per row, same order
  meta.json     model, chunker, dim, built_at, job count
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .chunkers import CHUNKERS, Chunk
from .embed import Embedder


@dataclass
class Hit:
    job_id: str
    score: float
    chunk_id: str
    text: str


class VectorIndex:
    def __init__(self, vectors: np.ndarray, chunks: list[Chunk], meta: dict[str, Any]) -> None:
        assert len(vectors) == len(chunks)
        self.vectors = vectors
        self.chunks = chunks
        self.meta = meta

    @classmethod
    def build(cls, jobs: Iterable[dict[str, Any]], embedder: Embedder, chunker: str) -> "VectorIndex":
        chunk_fn = CHUNKERS[chunker]
        chunks: list[Chunk] = []
        n_jobs = 0
        for job in jobs:
            n_jobs += 1
            chunks.extend(chunk_fn(job))
        vectors = embedder.encode([c.text for c in chunks], "passage") if chunks else np.zeros((0, embedder.dim), np.float32)
        meta = {"model": embedder.name, "chunker": chunker, "dim": int(vectors.shape[1]) if len(vectors) else embedder.dim,
                "jobs": n_jobs, "chunks": len(chunks), "built_at": datetime.now(timezone.utc).isoformat()}
        return cls(vectors.astype(np.float32), chunks, meta)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        with (directory / "chunks.jsonl").open("w", encoding="utf-8") as fh:
            for c in self.chunks:
                fh.write(json.dumps({"chunk_id": c.chunk_id, "job_id": c.job_id, "text": c.text}, ensure_ascii=False) + "\n")
        (directory / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "VectorIndex":
        vectors = np.load(directory / "vectors.npy")
        chunks = []
        with (directory / "chunks.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                chunks.append(Chunk(d["job_id"], d["chunk_id"], d["text"]))
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        return cls(vectors, chunks, meta)

    def search(self, query_vec: np.ndarray, k: int = 10, allowed_jobs: set[str] | None = None,
               per_job: bool = True) -> list[Hit]:
        """Cosine top-k. With per_job=True a job appears once, scored by its best chunk."""
        if not len(self.vectors):
            return []
        q = query_vec.reshape(-1).astype(np.float32)
        q /= np.linalg.norm(q) or 1.0
        scores = self.vectors @ q
        order = np.argsort(-scores)
        hits: list[Hit] = []
        seen: set[str] = set()
        for idx in order:
            c = self.chunks[int(idx)]
            if allowed_jobs is not None and c.job_id not in allowed_jobs:
                continue
            if per_job and c.job_id in seen:
                continue
            seen.add(c.job_id)
            hits.append(Hit(c.job_id, float(scores[idx]), c.chunk_id, c.text))
            if len(hits) >= k:
                break
        return hits
