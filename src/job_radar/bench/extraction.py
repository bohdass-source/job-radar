"""Extraction accuracy on 50 hand-checked jobs.

`label` samples jobs (stratified by source), pre-fills each with the current
extractor output and writes data/private/bench/extraction_gold.jsonl. You fix
the fields by hand and set "verified": true. `run` scores only verified rows.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from pymongo.database import Database

from ..config import private_path
from ..extract.extractor import extract_one
from ..extract.llm import LLMClient
from ..pipeline.dedup import city_key

GOLD = "bench/extraction_gold.jsonl"
FIELDS = ["seniority", "german_level", "english_level", "remote", "city", "salary", "years_experience_min"]


def label(db: Database, n: int = 50, seed: int = 7, llm: LLMClient | None = None) -> Path:
    path = private_path(GOLD)
    existing = {json.loads(l)["job_id"] for l in path.read_text().splitlines()} if path.exists() else set()
    by_source: dict[str, list[dict]] = defaultdict(list)
    for job in db.jobs.find({"description": {"$ne": ""}}):
        by_source[job["sources"][0]["source"]].append(job)
    rng = random.Random(seed)
    picked: list[dict] = []
    pools = [rng.sample(v, len(v)) for v in by_source.values()]
    while len(picked) + len(existing) < n and any(pools):
        for p in pools:                      # round-robin over sources = stratified sample
            if p and len(picked) + len(existing) < n:
                job = p.pop()
                if job["job_id"] not in existing:
                    picked.append(job)
    llm = llm or LLMClient()
    with path.open("a", encoding="utf-8") as fh:
        for job in picked:
            doc, method = extract_one(job, llm, "auto")
            fh.write(json.dumps({
                "job_id": job["job_id"], "verified": False, "prefilled_by": method,
                "title": job.get("title"), "company": job.get("company"),
                "text_preview": job.get("description", "")[:600],
                "gold": {k: doc.get(k) for k in FIELDS + ["skills_required", "skills_nice_to_have"]},
            }, ensure_ascii=False) + "\n")
    return path


def _salary_ok(pred: Any, gold: Any) -> bool:
    if not gold and not pred:
        return True
    if not gold or not pred:
        return False
    for k in ("min", "max"):
        g, p = gold.get(k), pred.get(k)
        if (g is None) != (p is None):
            return False
        if g is not None and abs(g - p) > 0.05 * g:
            return False
    return True


def _field_ok(field: str, pred: Any, gold: Any) -> bool:
    if field == "salary":
        return _salary_ok(pred, gold)
    if field == "city":
        return city_key(pred or "") == city_key(gold or "")
    return pred == gold


def _prf(pred: set[str], gold: set[str]) -> tuple[int, int, int]:
    return len(pred & gold), len(pred - gold), len(gold - pred)


def run(db: Database, methods: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
    path = private_path(GOLD)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    gold_rows = [r for r in rows if r.get("verified")]
    llm = LLMClient()
    methods = methods or (["rules", "llm"] if llm.available else ["rules"])
    results = []
    for method in methods:
        correct: dict[str, int] = defaultdict(int)
        tp = fp = fn = 0
        tp_all = fp_all = fn_all = 0
        n = 0
        for r in gold_rows:
            job = db.jobs.find_one({"job_id": r["job_id"]})
            if not job:
                continue
            pred, _ = extract_one(job, llm if method == "llm" else None, method)  # type: ignore[arg-type]
            g = r["gold"]
            n += 1
            for f in FIELDS:
                correct[f] += _field_ok(f, pred.get(f), g.get(f))
            a, b, c = _prf(set(pred.get("skills_required", [])), set(g.get("skills_required", [])))
            tp, fp, fn = tp + a, fp + b, fn + c
            all_p = set(pred.get("skills_required", [])) | set(pred.get("skills_nice_to_have", []))
            all_g = set(g.get("skills_required", [])) | set(g.get("skills_nice_to_have", []))
            a, b, c = _prf(all_p, all_g)
            tp_all, fp_all, fn_all = tp_all + a, fp_all + b, fn_all + c

        def f1(tp_: int, fp_: int, fn_: int) -> tuple[float, float, float]:
            p = tp_ / (tp_ + fp_) if tp_ + fp_ else 0.0
            rc = tp_ / (tp_ + fn_) if tp_ + fn_ else 0.0
            return p, rc, (2 * p * rc / (p + rc) if p + rc else 0.0)

        results.append({"method": method, "n": n, **{f: correct[f] / n if n else 0.0 for f in FIELDS},
                        "skills_req": f1(tp, fp, fn), "skills_all": f1(tp_all, fp_all, fn_all),
                        "model": llm.model if method == "llm" else "—"})
    return results, len(gold_rows)


def to_markdown(results: list[dict[str, Any]]) -> str:
    head = ("| Method | Jobs | Seniority | German | English | Remote | City | Salary | Years | Skills req. P / R / F1 | All skills F1 |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    lines = []
    for r in results:
        p, rc, f = r["skills_req"]
        name = r["method"] if r["method"] == "rules" else f"llm ({r['model']})"
        lines.append(f"| {name} | {r['n']} | " + " | ".join(f"{100 * r[f]:.0f}%" for f in FIELDS)
                     + f" | {p:.2f} / {rc:.2f} / {f:.2f} | {r['skills_all'][2]:.2f} |")
    return head + "\n" + "\n".join(lines) + "\n\n_Field accuracy = exact match with the gold label (city normalised, " \
        "salary within 5%). Skills compared after synonym normalisation. Gold labels: LLM annotator, see " \
        "[bench/LABELLING.md](bench/LABELLING.md)._"
