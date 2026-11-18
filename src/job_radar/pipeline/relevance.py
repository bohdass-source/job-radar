"""Is this ad an AI/ML job in Germany? Keyword rules from config/sources.yaml."""

from __future__ import annotations

import re
from functools import lru_cache

from ..config import load_yaml

# A title with one of these is an engineering/science role even if it also names
# an excluded area ("Marketing Data Scientist", "AI Engineer – Finance").
_STRONG = re.compile(r"(engineer|ingenieur|scientist|entwickler|developer|researcher|research|mlops|architekt|architect)", re.I)
_GERMANY_CODES = {"DE", "DEU", "GERMANY", "DEUTSCHLAND"}


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


@lru_cache
def _rules() -> tuple[re.Pattern[str], re.Pattern[str] | None, re.Pattern[str], int]:
    cfg = (load_yaml("sources.yaml") or {}).get("relevance", {})
    # (?<![\w]) instead of \b so "ML-Engineer" and "AI/ML" both match.
    title = re.compile(rf"(?<![\w])(?:{_alt(cfg.get('title_keywords', []))})(?![\w])", re.I)
    excl_words = cfg.get("title_exclude", [])
    exclude = re.compile(rf"(?:{_alt(excl_words)})", re.I) if excl_words else None
    body = re.compile(rf"(?<![\w])(?:{_alt(cfg.get('body_keywords', []))})(?![\w])", re.I)
    return title, exclude, body, int(cfg.get("body_min_hits", 2))


@lru_cache
def _geo() -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    cfg = (load_yaml("sources.yaml") or {}).get("geo", {})
    de = cfg.get("germany_markers") or []
    foreign = cfg.get("foreign_markers") or []
    return (re.compile(_alt(de), re.I) if de else None, re.compile(_alt(foreign), re.I) if foreign else None)


def _excluded(title: str) -> bool:
    _, exclude, _, _ = _rules()
    return bool(exclude and exclude.search(title) and not _STRONG.search(title))


def is_relevant_title(title: str) -> bool:
    title_re, _, _, _ = _rules()
    return not _excluded(title) and bool(title_re.search(title))


def is_relevant(title: str, text: str = "") -> bool:
    if is_relevant_title(title):
        return True
    if _excluded(title):
        return False
    _, _, body_re, min_hits = _rules()
    return len({m.group(0).lower() for m in body_re.finditer(text or "")}) >= min_hits


def is_germany(location: str, city: str = "", country: str | None = "DE") -> bool:
    if country and country.upper() not in _GERMANY_CODES:
        return False
    text = f" {location} {city} ".lower()
    de, foreign = _geo()
    if foreign and foreign.search(text):
        # "Berlin; London" stays: it names a German place too.
        return bool(de and de.search(text))
    return True
