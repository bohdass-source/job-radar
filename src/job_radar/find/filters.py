"""Search filters and sorting shared by all four search modes.

Every filter explains itself: `check()` returns the reasons a job was dropped,
so a mode can tell the user "dropped 312: 180 outside 50 km of Berlin, ...".
Unknown facts never drop a job (an ad that does not mention German is not
excluded by a German filter), they only lower its rank when sorting by fit.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import load_yaml
from ..extract.schema import LEVEL_ORDER
from ..models import utcnow
from ..pipeline.dedup import city_key, display_city
from ..pipeline.freshness import _aware
from ..i18n import t

Workplace = Literal["remote", "hybrid", "onsite"]
SortKey = Literal["fit", "date", "salary", "language", "distance"]
SENIORITY = ["intern", "junior", "mid", "senior", "lead"]


class SearchFilters(BaseModel):
    cities: list[str] = Field(default_factory=list, description="empty = all of Germany")
    radius_km: int = Field(default=0, ge=0, le=800, description="0 = only the city itself")
    workplace: list[Workplace] = Field(default_factory=list, description="empty = any; e.g. ['remote', 'hybrid']")
    salary_min: int | None = Field(default=None, description="yearly EUR; jobs with a stated maximum below it are dropped")
    salary_max: int | None = Field(default=None, description="yearly EUR; jobs with a stated minimum above it are dropped")
    only_with_salary: bool = False
    german_max: str | None = Field(default=None, description="your German level; jobs needing more are dropped")
    english_max: str | None = None
    seniority: list[str] = Field(default_factory=list)
    no_agency: bool = False
    fulltime: bool | None = None          # True = full time only, False = part time only
    permanent_only: bool = False
    career_changer_ok: bool = False      # BA flag "Quereinstieg geeignet"
    exclude_internships: bool = False
    posted_within_days: int | None = None
    exclude_companies: list[str] = Field(default_factory=list)
    keywords_all: list[str] = Field(default_factory=list, description="every keyword must appear")
    keywords_any: list[str] = Field(default_factory=list)
    keywords_none: list[str] = Field(default_factory=list)
    title_regex: str | None = Field(default=None, description="only jobs whose title matches (role families)")
    sort: SortKey = "fit"

    def describe(self) -> str:
        parts = []
        if self.cities:
            parts.append(", ".join(self.cities) + (" + " + t("f.km", km=self.radius_km) if self.radius_km else ""))
        if self.workplace:
            parts.append(t("f.workplace", v="/".join(self.workplace)))
        if self.salary_min or self.salary_max:
            parts.append(t("f.salary", lo=self.salary_min or "…", hi=self.salary_max or "…"))
        if self.german_max:
            parts.append(t("f.german", v=self.german_max))
        if self.english_max:
            parts.append(t("f.english", v=self.english_max))
        if self.seniority:
            parts.append(t("f.seniority", v="/".join(self.seniority)))
        flags = [t(n) for n, on in [("f.no_agency", self.no_agency), ("f.permanent", self.permanent_only),
                                    ("f.career_changer", self.career_changer_ok),
                                    ("f.no_internships", self.exclude_internships)] if on]
        if self.fulltime is not None:
            flags.append(t("f.fulltime") if self.fulltime else t("f.parttime"))
        if self.posted_within_days:
            flags.append(t("f.days", n=self.posted_within_days))
        return "; ".join(parts + flags) or t("f.none")


@lru_cache
def _search_cfg() -> dict[str, Any]:
    return load_yaml("search.yaml") or {}


@lru_cache
def _coords() -> dict[str, tuple[float, float]]:
    return {city_key(k): (float(v[0]), float(v[1])) for k, v in (_search_cfg().get("city_coords") or {}).items()}


def city_coords(city: str) -> tuple[float, float] | None:
    return _coords().get(city_key(city))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def job_coords(job: dict[str, Any]) -> tuple[float, float] | None:
    attrs = job.get("attrs") or {}
    if attrs.get("lat") and attrs.get("lon"):
        return float(attrs["lat"]), float(attrs["lon"])
    ext = job.get("extraction") or {}
    return city_coords(ext.get("city") or job.get("city") or "")


def workplace_of(job: dict[str, Any]) -> str:
    ext = job.get("extraction") or {}
    wp = ext.get("remote") or "unknown"
    attrs = job.get("attrs") or {}
    if wp == "unknown" and attrs.get("homeoffice") is True:
        wp = "hybrid"
    if wp == "unknown" and attrs.get("homeoffice") is False:
        wp = "onsite"
    if wp == "unknown" and job.get("remote"):
        wp = "remote"
    return wp


def yearly_salary(job: dict[str, Any]) -> tuple[float | None, float | None]:
    s = (job.get("extraction") or {}).get("salary") or {}
    lo, hi = s.get("min"), s.get("max")
    if lo is None and hi is None:
        lo, hi = job.get("salary_min"), job.get("salary_max")
        period = job.get("salary_period") or "year"
    else:
        period = s.get("period") or "year"
    f = {"year": 1, "month": 12, "hour": 1720}.get(period, 1)
    return (lo * f if lo else None, hi * f if hi else None)


def is_agency(job: dict[str, Any]) -> bool:
    cfg = _search_cfg()
    company = (job.get("company") or "").lower()
    text = (job.get("description") or "")[:4000].lower()
    return any(m in company for m in cfg.get("agency_company_markers", [])) or \
        any(m in text for m in cfg.get("agency_text_markers", []))


def _level(v: str | None) -> int | None:
    return LEVEL_ORDER.index(v) if v in LEVEL_ORDER else None


def _contains(text: str, kw: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(kw.lower())}(?![\w])", text) is not None


def distance_km(job: dict[str, Any], f: SearchFilters) -> float | None:
    jc = job_coords(job)
    centres = [c for c in (city_coords(x) for x in f.cities) if c]
    if not jc or not centres:
        return None
    return min(haversine_km(jc, c) for c in centres)


def check(job: dict[str, Any], f: SearchFilters) -> list[str]:
    """Reasons this job is filtered out (empty list = keep)."""
    reasons: list[str] = []
    ext = job.get("extraction") or {}
    attrs = job.get("attrs") or {}
    wp = workplace_of(job)

    if f.cities and wp != "remote":
        jcity = city_key(ext.get("city") or job.get("city") or "")
        wanted = {city_key(c) for c in f.cities}
        if jcity not in wanted:
            d = distance_km(job, f)
            if f.radius_km and d is not None:
                if d > f.radius_km:
                    reasons.append(t("r.city"))
            elif jcity or d is not None:
                reasons.append(t("r.city"))
    if f.workplace and wp != "unknown" and wp not in f.workplace:
        reasons.append(t("r.workplace"))
    lo, hi = yearly_salary(job)
    if f.only_with_salary and lo is None and hi is None:
        reasons.append(t("r.no_salary"))
    if f.salary_min and (hi or lo) and (hi or lo) < f.salary_min:
        reasons.append(t("r.salary_low"))
    if f.salary_max and lo and lo > f.salary_max:
        reasons.append(t("r.salary_high"))
    for field, mine, label in (("german_level", f.german_max, "r.german"), ("english_level", f.english_max, "r.english")):
        need, have = _level(ext.get(field)), _level(mine)
        if need is not None and have is not None and need > have:
            reasons.append(t(label))
    if f.seniority and ext.get("seniority") in SENIORITY and ext["seniority"] not in f.seniority:
        reasons.append(t("r.seniority"))
    if f.exclude_internships and (ext.get("seniority") == "intern" or attrs.get("minor_job")):
        reasons.append(t("r.internship"))
    if f.no_agency and is_agency(job):
        reasons.append(t("r.agency"))
    if f.fulltime is True and attrs.get("fulltime") is False:
        reasons.append(t("r.not_fulltime"))
    if f.fulltime is False and attrs.get("parttime") is False and attrs.get("fulltime") is True:
        reasons.append(t("r.no_parttime"))
    if f.permanent_only and attrs.get("contract") == "BEFRISTET":
        reasons.append(t("r.fixed_term"))
    if f.career_changer_ok and attrs.get("career_changer_ok") is False:
        reasons.append(t("r.not_career_changer"))
    if f.posted_within_days:
        posted = _aware(job.get("posted_at")) or _aware(job.get("first_seen"))
        if posted and utcnow() - posted > timedelta(days=f.posted_within_days):
            reasons.append(t("r.old"))
    if f.title_regex and not re.search(f.title_regex, job.get("title", ""), re.I):
        reasons.append(t("r.other_role"))
    company = (job.get("company") or "").lower()
    if any(c.lower() in company for c in f.exclude_companies if c):
        reasons.append(t("r.company"))
    if f.keywords_all or f.keywords_any or f.keywords_none:
        text = f"{job.get('title', '')}\n{job.get('company', '')}\n{job.get('description', '')}".lower()
        if f.keywords_all and not all(_contains(text, k) for k in f.keywords_all):
            reasons.append(t("r.kw_all"))
        if f.keywords_any and not any(_contains(text, k) for k in f.keywords_any):
            reasons.append(t("r.kw_any"))
        if f.keywords_none and any(_contains(text, k) for k in f.keywords_none):
            reasons.append(t("r.kw_none"))
    return reasons


def apply(jobs: list[dict[str, Any]], f: SearchFilters) -> tuple[list[dict[str, Any]], Counter[str]]:
    kept, dropped = [], Counter()
    for job in jobs:
        reasons = check(job, f)
        if reasons:
            dropped[reasons[0]] += 1
        else:
            kept.append(job)
    return kept, dropped


def language_rank(job: dict[str, Any]) -> tuple[int, int]:
    """Lower = easier on languages: English ad and no German first, then by German level needed."""
    ext = job.get("extraction") or {}
    german = _level(ext.get("german_level"))
    if german is None:   # unknown: a German-language ad most likely needs German
        german = LEVEL_ORDER.index("B2") if job.get("language") == "de" else 1
    return german, 0 if job.get("language") == "en" else 1


def sort_jobs(items: list[tuple[dict[str, Any], float]], f: SearchFilters) -> list[tuple[dict[str, Any], float]]:
    """items: (job, fit score). Fit stays the tie-breaker for every other key."""
    if f.sort == "date":
        return sorted(items, key=lambda t: (-(_aware(t[0].get("posted_at")) or _aware(t[0].get("first_seen"))).timestamp(), -t[1]))
    if f.sort == "salary":
        return sorted(items, key=lambda t: (-(yearly_salary(t[0])[1] or yearly_salary(t[0])[0] or 0), -t[1]))
    if f.sort == "language":
        return sorted(items, key=lambda t: (*language_rank(t[0]), -t[1]))
    if f.sort == "distance":
        return sorted(items, key=lambda t: (distance_km(t[0], f) if distance_km(t[0], f) is not None else 9999, -t[1]))
    return sorted(items, key=lambda t: -t[1])


def city_label(job: dict[str, Any]) -> str:
    return display_city((job.get("extraction") or {}).get("city") or job.get("city") or job.get("location") or "")
