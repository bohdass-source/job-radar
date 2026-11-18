"""Retrieval benchmark: 3 chunkers x 2 embedding models on 40 labelled queries.

Labels are made by *pooling* (as in TREC): for every query the top-10 jobs of
all six configurations are merged, and only that pool is judged. Jobs nobody
retrieved count as not relevant. This keeps labelling to a few hundred
judgements instead of 40 x N.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml
from pymongo.database import Database

from ..config import ROOT, private_path
from ..search.chunkers import CHUNKERS
from ..search.embed import MODELS, Embedder, get_embedder
from ..search.index import VectorIndex

QRELS = "bench/qrels.json"


def load_queries() -> list[dict[str, str]]:
    return yaml.safe_load((ROOT / "bench" / "queries.yaml").read_text(encoding="utf-8"))["queries"]


def corpus(db: Database) -> list[dict[str, Any]]:
    return list(db.jobs.find({"description": {"$ne": ""}}, {"job_id": 1, "title": 1, "company": 1, "description": 1}))


def build_all(db: Database, models: list[str] | None = None,
              embedders: dict[str, Embedder] | None = None) -> dict[tuple[str, str], tuple[VectorIndex, float]]:
    jobs = corpus(db)
    out = {}
    for model in models or list(MODELS):
        emb = (embedders or {}).get(model) or get_embedder(model)
        for chunker in CHUNKERS:
            t0 = time.perf_counter()
            idx = VectorIndex.build(jobs, emb, chunker)
            out[(chunker, model)] = (idx, time.perf_counter() - t0)
    return out


def _rank(idx: VectorIndex, emb: Embedder, query: str, k: int) -> list[str]:
    return [h.job_id for h in idx.search(emb.encode([query], "query")[0], k=k)]


def recall_at(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def mrr_at(ranked: list[str], relevant: set[str], k: int) -> float:
    for i, jid in enumerate(ranked[:k], 1):
        if jid in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / np.log2(i + 1) for i, j in enumerate(ranked[:k], 1) if j in relevant)
    ideal = sum(1.0 / np.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal if ideal else 0.0


def pool(db: Database, indexes: dict, embedders: dict[str, Embedder], depth: int = 10) -> dict[str, list[str]]:
    pooled: dict[str, list[str]] = {}
    for q in load_queries():
        seen: list[str] = []
        for (chunker, model), (idx, _) in indexes.items():
            for jid in _rank(idx, embedders[model], q["text"], depth):
                if jid not in seen:
                    seen.append(jid)
        pooled[q["id"]] = seen
    return pooled


def label(db: Database, judge: Callable[[str, dict[str, Any]], bool], judge_name: str,
          models: list[str] | None = None) -> Path:
    models = models or list(MODELS)
    embedders = {m: get_embedder(m) for m in models}
    indexes = build_all(db, models, embedders)
    pooled = pool(db, indexes, embedders)
    path = private_path(QRELS)
    qrels = json.loads(path.read_text()) if path.exists() else {}
    jobs = {j["job_id"]: j for j in corpus(db)}
    for q in load_queries():
        entry = qrels.setdefault(q["id"], {"query": q["text"], "relevant": [], "judged": [], "judge": judge_name})
        for jid in pooled[q["id"]]:
            if jid in entry["judged"]:
                continue
            if judge(q["text"], jobs[jid]):
                entry["relevant"].append(jid)
            entry["judged"].append(jid)
            path.write_text(json.dumps(qrels, indent=1), encoding="utf-8")  # save as you go
    return path


def run(db: Database, models: list[str] | None = None, embedders: dict[str, Embedder] | None = None,
        k: int = 10) -> list[dict[str, Any]]:
    qrels = json.loads(private_path(QRELS).read_text(encoding="utf-8"))
    models = models or list(MODELS)
    embedders = embedders or {m: get_embedder(m) for m in models}
    indexes = build_all(db, models, embedders)
    queries = [q for q in load_queries() if qrels.get(q["id"], {}).get("relevant")]
    rows = []
    for (chunker, model), (idx, build_s) in indexes.items():
        r5, r10, mrr, ndcg, lat = [], [], [], [], []
        for q in queries:
            rel = set(qrels[q["id"]]["relevant"])
            t0 = time.perf_counter()
            ranked = _rank(idx, embedders[model], q["text"], k)
            lat.append(time.perf_counter() - t0)
            r5.append(recall_at(ranked, rel, 5))
            r10.append(recall_at(ranked, rel, 10))
            mrr.append(mrr_at(ranked, rel, 10))
            ndcg.append(ndcg_at(ranked, rel, 10))
        rows.append({"chunker": chunker, "model": model, "queries": len(queries), "chunks": len(idx.chunks),
                     "recall@5": float(np.mean(r5)) if r5 else 0.0, "recall@10": float(np.mean(r10)) if r10 else 0.0,
                     "mrr@10": float(np.mean(mrr)) if mrr else 0.0, "ndcg@10": float(np.mean(ndcg)) if ndcg else 0.0,
                     "build_s": build_s, "query_ms": 1000 * float(np.median(lat)) if lat else 0.0})
    rows.sort(key=lambda r: -r["ndcg@10"])
    return rows


def to_markdown(rows: list[dict[str, Any]], judge: str, n_jobs: int) -> str:
    head = ("| Chunker | Model | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Chunks | Index build, s | Query, ms |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|---:|")
    body = "\n".join(
        f"| {r['chunker']} | {r['model']} | {r['recall@5']:.3f} | {r['recall@10']:.3f} | {r['mrr@10']:.3f} | "
        f"{r['ndcg@10']:.3f} | {r['chunks']} | {r['build_s']:.1f} | {r['query_ms']:.1f} |" for r in rows)
    q = rows[0]["queries"] if rows else 0
    return (f"{head}\n{body}\n\n_{q} queries with at least one relevant job, corpus of {n_jobs} jobs, "
            f"pooled top-10 judgements by {judge}. Models: minilm = {MODELS['minilm']}, e5 = {MODELS['e5']}._")
