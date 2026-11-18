"""`extract`: turn each open job into a validated JobExtraction (LLM first, rules as fallback)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pymongo.database import Database

from ..models import utcnow
from ..skills import all_skills, normalise
from .llm import LLMClient, LLMInvalidOutput, LLMUnavailable
from .rules import extract_rules
from .schema import JobExtraction, json_schema_for_prompt

log = logging.getLogger(__name__)

Method = Literal["auto", "llm", "rules"]

SYSTEM = """You extract structured facts from one job ad (German or English) for a job-market analysis.
Rules:
- Answer with ONE JSON object that validates against the JSON schema below. No prose, no markdown.
- Use only facts stated in the ad. If something is not stated, use "unknown" (or null for optional fields).
- seniority: intern = Praktikum/Werkstudent/thesis; lead = lead/staff/principal/head.
- german_level / english_level: CEFR. "verhandlungssicher"/"fließend"/"fluent" = C1, "Muttersprache"/"native" = C2,
  "gute Kenntnisse"/"good" = B2, "Grundkenntnisse"/"basic" = A2. If the ad says the language is optional or a plus,
  use "none". If the language is not mentioned, use "unknown" (even if the ad itself is written in it).
- skills_required: must-have technical skills; skills_nice_to_have: those marked as plus/von Vorteil/nice to have.
  Use short canonical names; prefer these when they apply: {skills}.
- remote: remote only for fully remote; hybrid if some home office; onsite if presence is required.
- salary only if a number is stated (not estimated); period year/month/hour.
- city: main work city in Germany, German spelling (München, Köln), or null.
JSON schema:
{schema}"""


def _system_prompt() -> str:
    return SYSTEM.format(skills=", ".join(all_skills()), schema=json_schema_for_prompt())


def _user_prompt(job: dict[str, Any]) -> str:
    text = job.get("description", "")[:7000]
    snippet_note = "\n(Note: only a snippet of the ad is available.)" if job.get("description_is_snippet") else ""
    return (f"Title: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
            f"Location: {job.get('location') or job.get('city', '')}{snippet_note}\n\nAd text:\n{text}")


def to_document(ext: JobExtraction) -> dict[str, Any]:
    req, req_unknown = normalise(ext.skills_required)
    nice, nice_unknown = normalise(ext.skills_nice_to_have)
    nice = [s for s in nice if s not in req]
    doc = ext.model_dump(mode="json")
    doc.update(skills_required=req, skills_nice_to_have=nice, skills_other=req_unknown + nice_unknown)
    return doc


def extract_one(job: dict[str, Any], llm: LLMClient | None, method: Method = "auto") -> tuple[dict[str, Any], str]:
    if method in ("auto", "llm") and llm is not None and llm.available:
        try:
            ext = llm.complete_json(_system_prompt(), _user_prompt(job), JobExtraction)
            return to_document(ext), "llm"
        except (LLMUnavailable, LLMInvalidOutput) as exc:
            if method == "llm":
                raise
            log.warning("LLM extraction failed for %s (%s); using rules", job.get("job_id"), exc)
    ext = extract_rules(job.get("title", ""), job.get("description", ""), job.get("city"))
    return to_document(ext), "rules"


def extract_all(db: Database, *, method: Method = "auto", limit: int | None = None, force: bool = False) -> dict[str, int]:
    llm = LLMClient() if method != "rules" else None
    query: dict[str, Any] = {"status": {"$in": ["open", "unverified"]}}
    if not force:
        query["$or"] = [{"extraction": None}, {"extraction_stale": True}]
    counts = {"llm": 0, "rules": 0, "failed": 0}
    cursor = db.jobs.find(query)
    if limit:
        cursor = cursor.limit(limit)
    for job in cursor:
        try:
            doc, used = extract_one(job, llm, method)
        except Exception as exc:
            log.error("extraction failed for %s: %s", job["job_id"], exc)
            counts["failed"] += 1
            continue
        counts[used] += 1
        db.jobs.update_one({"job_id": job["job_id"]}, {"$set": {
            "extraction": doc, "extraction_method": used, "extraction_model": llm.model if used == "llm" and llm else None,
            "extracted_at": utcnow(), "extraction_stale": False}})
    return counts
