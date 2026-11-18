"""Three chunking strategies, written from scratch, compared in the benchmark.

- fixed:    sliding window of N words with overlap. Blind to structure.
- sentence: packs whole sentences up to a word budget. Never cuts a sentence.
- section:  splits on the headings job ads use (Aufgaben / Profil / Requirements /
            Benefits ...) and prefixes every chunk with "title @ company", so a
            chunk that only says "PyTorch, 3+ years" still knows which job it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Chunk:
    job_id: str
    chunk_id: str
    text: str


def _words(text: str) -> list[str]:
    return text.split()


def fixed_chunks(job_id: str, title: str, text: str, size: int = 120, overlap: int = 30) -> list[Chunk]:
    words = _words(f"{title}. {text}")
    if not words:
        return []
    step = max(1, size - overlap)
    out = []
    for i, start in enumerate(range(0, max(1, len(words) - overlap), step)):
        out.append(Chunk(job_id, f"{job_id}:f{i}", " ".join(words[start:start + size])))
    return out


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9•\-])|\n+")


def sentence_chunks(job_id: str, title: str, text: str, budget: int = 100) -> list[Chunk]:
    sentences = [s.strip() for s in _SENT.split(f"{title}.\n{text}") if s and s.strip()]
    out: list[Chunk] = []
    buf: list[str] = []
    count = 0
    for sent in sentences:
        n = len(sent.split())
        if buf and count + n > budget:
            out.append(Chunk(job_id, f"{job_id}:s{len(out)}", " ".join(buf)))
            buf, count = [], 0
        buf.append(sent)
        count += n
    if buf:
        out.append(Chunk(job_id, f"{job_id}:s{len(out)}", " ".join(buf)))
    return out


_HEADING = re.compile(
    r"^\s*(?:#+\s*)?(?:"
    r"deine aufgaben|ihre aufgaben|aufgaben|das erwartet dich|was du tust|your tasks|tasks|responsibilities|"
    r"what you(?:'| wi)ll do|the role|about the role|your role|deine rolle|"
    r"dein profil|ihr profil|profil|was du mitbringst|das bringst du mit|anforderungen|qualifikation(?:en)?|"
    r"requirements|qualifications|what you bring|your profile|who you are|must[- ]have|"
    r"nice to have|von vorteil|bonus|"
    r"wir bieten|was wir bieten|benefits|what we offer|perks|"
    r"über uns|about us|wer wir sind|who we are|das sind wir"
    r")\s*:?\s*$",
    re.I | re.M,
)


def section_chunks(job_id: str, title: str, text: str, company: str = "", max_words: int = 160) -> list[Chunk]:
    header = f"{title} @ {company}".strip(" @")
    parts: list[tuple[str, str]] = []
    last, name = 0, "intro"
    for m in _HEADING.finditer(text):
        parts.append((name, text[last:m.start()]))
        name, last = m.group(0).strip().rstrip(":"), m.end()
    parts.append((name, text[last:]))
    out: list[Chunk] = []
    for sec_name, body in parts:
        words = body.split()
        if not words:
            continue
        for start in range(0, len(words), max_words):   # oversized sections are split, never dropped
            piece = " ".join(words[start:start + max_words])
            label = "" if sec_name == "intro" else f" | {sec_name}"
            out.append(Chunk(job_id, f"{job_id}:c{len(out)}", f"{header}{label}: {piece}"))
    if not out:
        out.append(Chunk(job_id, f"{job_id}:c0", header))
    return out


CHUNKERS: dict[str, Callable[..., list[Chunk]]] = {
    "fixed": lambda j: fixed_chunks(j["job_id"], j.get("title", ""), j.get("description", "")),
    "sentence": lambda j: sentence_chunks(j["job_id"], j.get("title", ""), j.get("description", "")),
    "section": lambda j: section_chunks(j["job_id"], j.get("title", ""), j.get("description", ""), j.get("company", "")),
}
