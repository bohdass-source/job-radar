"""Rule-based extractor. Runs without an LLM, serves as the benchmark baseline,
and fills gaps when the gateway is down."""

from __future__ import annotations

import re

from ..skills import find_in_text
from .schema import JobExtraction, LanguageLevel, RemotePolicy, Salary, Seniority

_SENIORITY = [
    (Seniority.INTERN, r"\b(intern|internship|praktikum|praktikant|werkstudent|working student|thesis|abschlussarbeit|masterarbeit)"),
    (Seniority.LEAD, r"\b(lead|head of|principal|staff|director|leiter|teamleitung|chapter lead)\b"),
    (Seniority.SENIOR, r"\b(senior|sr\.?|expert|experienced)\b"),
    (Seniority.JUNIOR, r"\b(junior|jr\.?|entry|graduate|absolvent|berufseinsteiger|trainee)\b"),
]

_LEVEL_WORDS = [
    (LanguageLevel.C2, r"(muttersprach\w*|native|c2)"),
    (LanguageLevel.C1, r"(verhandlungssicher\w*|fließend\w*|fliessend\w*|fluent|sehr gute\w*|excellent|business[- ]fluent|c1)"),
    (LanguageLevel.B2, r"(gute\w*|good|solid\w*|professional|b2)"),
    (LanguageLevel.B1, r"(basic|grundlegende\w*|b1|conversational)"),
    (LanguageLevel.A2, r"(grundkenntnisse|a2|a1)"),
]
_OPTIONAL = r"(von vorteil|wünschenswert|ein plus|is a plus|nice to have|beneficial|bonus|not required|nicht erforderlich|keine voraussetzung)"


def _language_level(text: str, words: str) -> LanguageLevel:
    """Find the sentence mentioning the language and read its level from it."""
    # Work on clauses, so "sehr gute Deutsch- und gute Englischkenntnisse" gives
    # each language its own level.
    clauses = [c for s in re.split(r"(?<=[.!?;\n])", text)
               for c in re.split(r",|\s(?:und|and|sowie|as well as|plus)\s", s, flags=re.I)]
    for sent in clauses:
        if not re.search(words, sent, re.I):
            continue
        if re.search(_OPTIONAL, sent, re.I):
            return LanguageLevel.NONE
        for level, pattern in _LEVEL_WORDS:
            if re.search(pattern, sent, re.I):
                return level
        return LanguageLevel.B2
    return LanguageLevel.UNKNOWN


def _remote(text: str) -> RemotePolicy:
    t = text.lower()
    if re.search(r"(100\s*% remote|fully remote|full remote|remote[- ]first|vollständig remote|komplett remote"
                 r"|ausschließlich remote|remote-only|remote \(deutschland\)|remote within germany)", t):
        return RemotePolicy.REMOTE
    if re.search(r"(hybrid|homeoffice|home office|home-office|mobiles arbeiten|mobile work|remote work|flexible remote)", t):
        return RemotePolicy.HYBRID
    if re.search(r"(on-site|onsite|vor ort|präsenz|in office|im büro)", t):
        return RemotePolicy.ONSITE
    return RemotePolicy.UNKNOWN


_NUM = r"(\d{2,3}(?:[.,]\d{3})|\d{2,3}\s?k)"


def _to_number(s: str) -> float:
    s = s.lower().replace(" ", "")
    if s.endswith("k"):
        return float(s[:-1]) * 1000
    return float(s.replace(".", "").replace(",", ""))


def _salary(text: str) -> Salary | None:
    m = re.search(rf"(?:€|eur|euro)?\s*{_NUM}\s*(?:€|eur)?\s*(?:-|–|bis|to)\s*(?:€|eur)?\s*{_NUM}\s*(?:€|eur|euro)", text, re.I) \
        or re.search(rf"(?:€|eur)\s*{_NUM}\s*(?:-|–|bis|to)\s*{_NUM}", text, re.I)
    if m:
        lo, hi = sorted((_to_number(m.group(1)), _to_number(m.group(2))))
        if 15_000 <= lo <= 400_000:
            return Salary(min=lo, max=hi, currency="EUR", period="year")
    return None


def _split_required_nice(text: str) -> tuple[str, str]:
    required, nice = [], []
    in_nice = False
    for line in text.splitlines():
        low = line.lower()
        is_heading = len(line) < 80 and not line.startswith("-")
        if re.search(_OPTIONAL + r"|nice-to-have|optional|plus points|pluspunkte", low):
            if is_heading:
                in_nice = True
                continue
            nice.append(line)
            continue
        if is_heading and re.search(r"(profil|requirements|qualifi|must|mitbringst|you bring|what you|anforderungen"
                                    r"|aufgaben|tasks|responsibilities|benefits|wir bieten|we offer)", low):
            in_nice = False
        (nice if in_nice else required).append(line)
    return "\n".join(required), "\n".join(nice)


def extract_rules(title: str, text: str, city: str | None = None) -> JobExtraction:
    full = f"{title}\n{text}"
    seniority = next((lvl for lvl, pat in _SENIORITY if re.search(pat, title, re.I)), None)
    if seniority is None:
        seniority = next((lvl for lvl, pat in _SENIORITY[1:] if re.search(pat, text[:600], re.I)), Seniority.UNKNOWN)
    req_text, nice_text = _split_required_nice(full)
    required = find_in_text(req_text)
    nice = [s for s in find_in_text(nice_text) if s not in required]
    years = re.search(r"(\d{1,2})\+?\s*(?:years|jahre|yrs)", text, re.I)
    return JobExtraction(
        seniority=seniority,
        skills_required=required,
        skills_nice_to_have=nice,
        german_level=_language_level(full, r"(deutsch|german)"),
        english_level=_language_level(full, r"(englisch|english)"),
        remote=_remote(full),
        salary=_salary(full),
        city=city or None,
        years_experience_min=int(years.group(1)) if years and int(years.group(1)) <= 30 else None,
        visa_sponsorship=True if re.search(r"visa sponsorship|visa support|relocation support", text, re.I) else None,
    )
