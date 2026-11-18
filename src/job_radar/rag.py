"""`ask`: MiniRAG over live job ads. Retrieve chunks, answer only from them, cite job ids."""

from __future__ import annotations

from dataclasses import dataclass

from pymongo.database import Database

from .extract.llm import LLMClient
from .search import DEFAULT_CHUNKER, index_dir, live_job_ids
from .search.embed import DEFAULT_MODEL, Embedder, get_embedder
from .search.index import Hit, VectorIndex

SYSTEM = """You answer questions about the German AI/ML job market using ONLY the job-ad excerpts provided.
Cite every claim with the job id in square brackets, e.g. [3f9a1c2b7d10]. If the excerpts do not contain the
answer, say so plainly. Answer in the language of the question. Be concise."""


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    used_llm: bool


def ask(db: Database, question: str, k: int = 8, embedder: Embedder | None = None,
        llm: LLMClient | None = None, index: VectorIndex | None = None) -> Answer:
    embedder = embedder or get_embedder(DEFAULT_MODEL)
    index = index or VectorIndex.load(index_dir(DEFAULT_CHUNKER, DEFAULT_MODEL))
    qv = embedder.encode([question], "query")[0]
    # Several chunks per job are fine here: they carry different facts.
    hits = index.search(qv, k=k, allowed_jobs=live_job_ids(db), per_job=False)
    if not hits:
        return Answer("Нет подходящих актуальных вакансий в индексе.", [], False)
    titles = {j["job_id"]: f"{j.get('title')} @ {j.get('company')}"
              for j in db.jobs.find({"job_id": {"$in": [h.job_id for h in hits]}}, {"job_id": 1, "title": 1, "company": 1})}
    context = "\n\n".join(f"[{h.job_id}] {titles.get(h.job_id, '')}\n{h.text}" for h in hits)
    llm = llm or LLMClient()
    if not llm.available:
        listing = "\n".join(f"- [{h.job_id}] {titles.get(h.job_id, '')} (score {h.score:.2f})" for h in hits)
        return Answer("LLM gateway не настроен; вот найденные фрагменты:\n" + listing, hits, False)
    text = llm.complete_text(SYSTEM, f"Question: {question}\n\nExcerpts:\n{context}")
    return Answer(text, hits, True)
