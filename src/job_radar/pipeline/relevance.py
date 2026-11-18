"""Is this ad an AI/ML job in Germany? Keyword rules from config/sources.yaml."""

from __future__ import annotations

import re
from functools import lru_cache

from ..config import load_yaml

# A title with one of these is an engineering/science role even if it also names
# an excluded area ("Marketing Data Scientist", "AI Engineer – Finance").
_STRONG = re.compile(r"(engineer|ingenieur|scientist|entwickler|developer|researcher|research|mlops|architekt|architect"
                     r"|entwicklung|programmier|data|software|informatik|phd|forschung"
                     # whole words only: "Steuerberater" is not an AI consultant
                     r"|(?<![a-zäöüß])(?:experte|expert|consultant|berater)(?![a-zäöüß]))", re.I)
_GERMANY_CODES = {"DE", "DEU", "GERMANY", "DEUTSCHLAND"}


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _words_re(words: list[str]) -> re.Pattern[str]:
    # (?<![\w]) instead of \b so "ML-Engineer" and "AI/ML" both match.
    return re.compile(rf"(?<![\w])(?:{_alt(words)})(?![\w])", re.I) if words else re.compile(r"(?!)")


@lru_cache
def _weak() -> re.Pattern[str]:
    return _words_re((load_yaml("sources.yaml") or {}).get("relevance", {}).get("title_weak_keywords", []))


@lru_cache
def _rules() -> tuple[re.Pattern[str], re.Pattern[str] | None, re.Pattern[str], int]:
    cfg = (load_yaml("sources.yaml") or {}).get("relevance", {})
    title = _words_re(cfg.get("title_keywords", []))
    excl_words = cfg.get("title_exclude", [])
    exclude = re.compile(rf"(?:{_alt(excl_words)})", re.I) if excl_words else None
    body = _words_re(cfg.get("body_keywords", []))
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


def _body_hits(text: str) -> int:
    _, _, body_re, _ = _rules()
    return len({m.group(0).lower() for m in body_re.finditer(text or "")})


def is_relevant_title(title: str) -> bool:
    """Title alone decides: a specific ML keyword, or a generic AI word plus a technical role."""
    title_re, _, _, _ = _rules()
    if _excluded(title):
        return False
    return bool(title_re.search(title)) or bool(_weak().search(title) and _STRONG.search(title))


def is_relevant(title: str, text: str = "") -> bool:
    if is_relevant_title(title):
        return True
    if _excluded(title):
        return False
    hits = _body_hits(text)
    _, _, _, min_hits = _rules()
    # A generic AI title ("AI Associate") needs one ML technology in the text; others need min_hits.
    return hits >= (1 if _weak().search(title) else min_hits)


def is_germany(location: str, city: str = "", country: str | None = "DE") -> bool:
    if country and country.upper() not in _GERMANY_CODES:
        return False
    text = f" {location} {city} ".lower()
    de, foreign = _geo()
    if foreign and foreign.search(text):
        # "Berlin; London" stays: it names a German place too.
        return bool(de and de.search(text))
    return True
