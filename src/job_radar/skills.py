"""Skill synonym dictionary: canonicalise names and find skills in free text."""

from __future__ import annotations

import re
from functools import lru_cache

from .config import load_yaml

# Short or ambiguous synonyms that would cause false hits in free text.
# They still canonicalise exact LLM outputs ("TF" -> tensorflow), but the
# text scanner ignores them.
_TEXT_UNSAFE = {"r", "go", "ts", "js", "tf", "py", "cv", "rl", "ml", "agents", "container", "containers", "elastic",
                "shell", "lightning", "prompting", "gpu", "evals", "nosql", "lambda", "s3"}


@lru_cache
def _table() -> tuple[dict[str, str], dict[str, str], re.Pattern[str]]:
    data = (load_yaml("skills.yaml") or {}).get("skills", {})
    alias: dict[str, str] = {}
    category: dict[str, str] = {}
    text_terms: dict[str, str] = {}
    for canon, spec in data.items():
        spec = spec or {}
        category[canon] = spec.get("category", "other")
        for name in [canon, *(spec.get("synonyms") or [])]:
            key = name.lower().strip()
            alias[key] = canon
            if key not in _TEXT_UNSAFE:
                text_terms[key] = canon
    ordered = sorted(text_terms, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w+#])(" + "|".join(re.escape(t) for t in ordered) + r")(?![\w+#])", re.I)
    return alias, category, pattern


def canonical(name: str) -> str | None:
    """Map any spelling to its canonical skill, or None if unknown."""
    alias, _, _ = _table()
    key = name.lower().strip().strip(".")
    if key in alias:
        return alias[key]
    key = re.sub(r"\s*\(.*?\)\s*", " ", key).strip()   # "PyTorch (advanced)"
    return alias.get(key)


def normalise(names: list[str]) -> tuple[list[str], list[str]]:
    """Return (known canonical skills, unknown raw names), both de-duplicated."""
    known: list[str] = []
    unknown: list[str] = []
    for n in names:
        c = canonical(n)
        if c and c not in known:
            known.append(c)
        elif not c and n.strip() and n.strip().lower() not in (u.lower() for u in unknown):
            unknown.append(n.strip())
    return known, unknown


def find_in_text(text: str) -> list[str]:
    _, _, pattern = _table()
    found: list[str] = []
    alias, _, _ = _table()
    for m in pattern.finditer(text or ""):
        c = alias[m.group(1).lower()]
        if c not in found:
            found.append(c)
    return found


def category_of(skill: str) -> str:
    return _table()[1].get(skill, "other")


def all_skills() -> list[str]:
    return list(_table()[1])
