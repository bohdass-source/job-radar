"""Cross-source duplicate detection.

The same job often arrives three times: from the company's ATS feed, from an
aggregator, and from a LinkedIn alert. Titles differ in gender markers and
punctuation, companies in legal suffixes, cities in language (München/Munich).
We normalise all three, block candidates by company, and merge when titles are
near-identical and locations are compatible.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_LEGAL = re.compile(
    r"\b(gmbh|mbh|ag|se|kg|kgaa|ohg|ug|haftungsbeschrankt|co|inc|ltd|llc|plc|bv|b\.v|sa|sas|corp|corporation"
    r"|group|gruppe|holding|germany|deutschland)\b\.?",
)
_GENDER = re.compile(
    r"(?<![a-z])\(?\s*(?:[mwfdx]\s*/\s*[mwfdx](?:\s*/\s*[mwfdx])?|all genders?|alle geschlechter|gn\*?|w\*m\*d|m\*w\*d)\s*\)?(?![a-z])",
    re.I,
)
_CITY_ALIASES = {
    "munchen": "munich", "muenchen": "munich", "koln": "cologne", "koeln": "cologne",
    "nurnberg": "nuremberg", "nuernberg": "nuremberg", "frankfurt am main": "frankfurt",
    "frankfurt a.m.": "frankfurt", "frankfurt/main": "frankfurt", "dusseldorf": "duesseldorf",
    "hannover": "hanover", "braunschweig": "brunswick",
}
_TITLE_SYNONYMS = [
    (re.compile(r"\bml\b"), "machine learning"),
    (re.compile(r"\bki\b"), "ai"),
    (re.compile(r"\bsr\.?\b"), "senior"),
    (re.compile(r"\bjr\.?\b"), "junior"),
]
_STOP = {"in", "for", "and", "und", "the", "der", "die", "das", "-", "mit", "with"}


def strip_accents(s: str) -> str:
    s = s.replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def company_key(name: str) -> str:
    s = strip_accents(name.lower())
    s = s.replace("&", " ")
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def title_key(title: str) -> str:
    s = _GENDER.sub(" ", title)
    s = strip_accents(s.lower())
    s = re.sub(r"[*:]in(nen)?\b", "", s)          # Entwickler*in, Entwickler:innen
    for pattern, repl in _TITLE_SYNONYMS:
        s = pattern.sub(repl, s)
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return " ".join(w for w in s.split() if w not in _STOP)


def city_key(city: str) -> str:
    s = strip_accents(city.lower()).strip()
    s = re.split(r"[,(]", s)[0].strip()
    return _CITY_ALIASES.get(s, s)


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_same_job(a: dict, b: dict, threshold: float = 0.75) -> bool:
    """a, b: dicts with company_key, title_key, city_key, remote."""
    if not a["company_key"] or a["company_key"] != b["company_key"]:
        return False
    if title_similarity(a["title_key"], b["title_key"]) < threshold:
        return False
    ca, cb = a.get("city_key", ""), b.get("city_key", "")
    return not ca or not cb or ca == cb or bool(a.get("remote")) or bool(b.get("remote"))


def make_job_id(company: str, title: str, city: str) -> str:
    raw = f"{company_key(company)}|{title_key(title)}|{city_key(city)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


_DISPLAY = {"munich": "München", "cologne": "Köln", "nuremberg": "Nürnberg", "frankfurt": "Frankfurt am Main",
            "duesseldorf": "Düsseldorf", "hanover": "Hannover", "brunswick": "Braunschweig"}


def display_city(city: str) -> str:
    """One readable name per city for reports: 'Munich', 'München, Bayern' -> 'München'."""
    key = city_key(city)
    if not key:
        return ""
    if key in _DISPLAY:
        return _DISPLAY[key]
    first = re.split(r"[,(]", city)[0].strip()
    return first[:1].upper() + first[1:]
