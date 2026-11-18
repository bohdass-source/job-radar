"""Company career pages via the public job-board feeds of their applicant-tracking systems.

Every ATS feed lists *all* currently published jobs of one company, so it is the
strongest freshness signal we have: a job missing from a successfully read feed
has been unpublished. Companies come from config/companies.yaml.
"""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterator

from ..config import load_yaml
from ..models import Posting
from ..pipeline.relevance import is_relevant_title
from .base import CheckResult, SourceAdapter


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).replace(" UTC", "+00:00").replace("Z", "+00:00")
    for candidate in (s, s.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def companies_for(ats: str) -> list[dict[str, Any]]:
    data = load_yaml("companies.yaml")
    return [c for c in data.get("companies", []) or [] if c.get("ats") == ats and c.get("enabled", True)]


class ATSAdapter(SourceAdapter):
    """Iterates the companies configured for this ATS; each company is one scope."""

    def companies(self) -> list[dict[str, Any]]:
        return self.cfg.get("companies") or companies_for(self.name)

    def fetch(self) -> Iterator[Posting]:
        for company in self.companies():
            slug = company["slug"]
            try:
                yield from self.fetch_company(company)
            except Exception as exc:  # one broken company must not stop the others
                self.errors.append(f"{slug}: {exc}")
                continue
            self.completed_scopes.add(slug)

    @property
    def errors(self) -> list[str]:
        if not hasattr(self, "_errors"):
            self._errors: list[str] = []
        return self._errors

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        raise NotImplementedError


class GreenhouseAdapter(ATSAdapter):
    """https://developers.greenhouse.io/job-board.html"""

    name = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        resp = self.http.get(self.BASE.format(token=company["slug"]), params={"content": "true"})
        resp.raise_for_status()
        for j in resp.json().get("jobs", []):
            yield Posting(
                source=self.name, source_id=str(j["id"]), scope=company["slug"],
                title=j.get("title", ""), company=company.get("name", company["slug"]),
                location=(j.get("location") or {}).get("name", ""),
                city=(j.get("location") or {}).get("name", "").split(",")[0],
                url=j.get("absolute_url", ""),
                description=html.unescape(j.get("content", "")),  # Greenhouse HTML-escapes content
                posted_at=_iso(j.get("first_published") or j.get("updated_at")),
                extra={"departments": [d.get("name") for d in j.get("departments", [])]},
            )

    def check(self, posting: dict[str, Any]) -> CheckResult:
        url = self.BASE.format(token=posting["scope"]) + f"/{posting['source_id']}"
        code = self.http.get(url).status_code
        return "closed" if code == 404 else "open" if code == 200 else "unknown"


class LeverAdapter(ATSAdapter):
    """https://github.com/lever/postings-api — set `eu: true` for api.eu.lever.co."""

    name = "lever"
    PAGE = 100

    def _base(self, company: dict[str, Any]) -> str:
        host = "api.eu.lever.co" if company.get("eu") else "api.lever.co"
        return f"https://{host}/v0/postings/{company['slug']}"

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        skip = 0
        while True:
            resp = self.http.get(self._base(company), params={"mode": "json", "skip": skip, "limit": self.PAGE})
            resp.raise_for_status()
            items = resp.json()
            for p in items:
                cats = p.get("categories") or {}
                parts = [p.get("descriptionPlain", "")]
                for block in p.get("lists", []) or []:
                    parts.append(block.get("text", ""))
                    parts.append(block.get("content", ""))
                parts.append(p.get("additionalPlain", ""))
                salary = p.get("salaryRange") or {}
                yield Posting(
                    source=self.name, source_id=p["id"], scope=company["slug"],
                    title=p.get("text", ""), company=company.get("name", company["slug"]),
                    location=cats.get("location", ""), city=(cats.get("location") or "").split(",")[0],
                    country=p.get("country") or "DE",
                    remote={"remote": True, "on-site": False}.get(p.get("workplaceType", "")),
                    url=p.get("hostedUrl", ""), apply_url=p.get("applyUrl", ""),
                    description="\n".join(x for x in parts if x),
                    posted_at=datetime.fromtimestamp(p["createdAt"] / 1000, tz=timezone.utc)
                    if p.get("createdAt") else None,
                    salary_min=salary.get("min"), salary_max=salary.get("max"),
                    salary_currency=salary.get("currency"),
                    salary_period={"per-year-salary": "year", "per-month-salary": "month",
                                   "per-hour-wage": "hour"}.get(salary.get("interval", "")),
                    extra={"workplace_type": p.get("workplaceType"), "team": cats.get("team")},
                )
            if len(items) < self.PAGE:
                return
            skip += self.PAGE

    def check(self, posting: dict[str, Any]) -> CheckResult:
        company = next((c for c in self.companies() if c["slug"] == posting["scope"]), {"slug": posting["scope"]})
        code = self.http.get(f"{self._base(company)}/{posting['source_id']}", params={"mode": "json"}).status_code
        return "closed" if code == 404 else "open" if code == 200 else "unknown"


class RecruiteeAdapter(ATSAdapter):
    """https://docs.recruitee.com/reference/offers"""

    name = "recruitee"

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        resp = self.http.get(f"https://{company['slug']}.recruitee.com/api/offers/")
        resp.raise_for_status()
        for o in resp.json().get("offers", []):
            if o.get("status") not in (None, "published"):
                continue
            locs = o.get("locations") or []
            salary = o.get("salary") or {}
            yield Posting(
                source=self.name, source_id=str(o["id"]), scope=company["slug"],
                title=o.get("title", ""), company=o.get("company_name") or company.get("name", ""),
                location=", ".join(filter(None, [locs[0].get("city"), locs[0].get("country")])) if locs else "",
                city=locs[0].get("city", "") if locs else o.get("city", ""),
                country=(locs[0].get("country_code") if locs else None) or "DE",
                remote=True if o.get("remote") else (False if o.get("on_site") and not o.get("hybrid") else None),
                url=o.get("careers_url", ""), apply_url=o.get("careers_apply_url", ""),
                description=f"{o.get('description', '')}\n{o.get('requirements', '')}",
                posted_at=_iso(o.get("published_at") or o.get("created_at")),
                salary_min=_num(salary.get("min")), salary_max=_num(salary.get("max")),
                salary_currency=salary.get("currency"), salary_period=salary.get("period"),
                extra={"hybrid": o.get("hybrid"), "close_at": o.get("close_at")},
            )


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class SmartRecruitersAdapter(ATSAdapter):
    """https://developers.smartrecruiters.com/docs/posting-api — list, then detail for relevant titles."""

    name = "smartrecruiters"
    BASE = "https://api.smartrecruiters.com/v1/companies/{cid}/postings"
    PAGE = 100

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        base = self.BASE.format(cid=company["slug"])
        offset = 0
        while True:
            resp = self.http.get(base, params={"limit": self.PAGE, "offset": offset, **(company.get("params") or {})})
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            for p in content:
                # Detail calls cost one request each, so only fetch them for relevant titles.
                if not is_relevant_title(p.get("name", "")):
                    continue
                detail = self.http.get(f"{base}/{p['id']}")
                d = detail.json() if detail.status_code == 200 else {}
                sections = ((d.get("jobAd") or {}).get("sections") or {})
                text = "\n".join((sections.get(k) or {}).get("text", "") for k in
                                 ("jobDescription", "qualifications", "additionalInformation"))
                loc = p.get("location") or {}
                yield Posting(
                    source=self.name, source_id=str(p["id"]), scope=company["slug"],
                    title=p.get("name", ""), company=(p.get("company") or {}).get("name") or company.get("name", ""),
                    location=", ".join(filter(None, [loc.get("city"), loc.get("country")])),
                    city=loc.get("city", ""), country=(loc.get("country") or "de").upper(),
                    remote=loc.get("remote"),
                    url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{company['slug']}/{p['id']}",
                    apply_url=d.get("applyUrl", ""), description=text,
                    posted_at=_iso(p.get("releasedDate")),
                    extra={"experience": (p.get("experienceLevel") or {}).get("label")},
                )
            offset += self.PAGE
            if offset >= int(data.get("totalFound", 0)) or not content:
                return

    def check(self, posting: dict[str, Any]) -> CheckResult:
        code = self.http.get(f"{self.BASE.format(cid=posting['scope'])}/{posting['source_id']}").status_code
        return "closed" if code == 404 else "open" if code == 200 else "unknown"


class AshbyAdapter(ATSAdapter):
    """https://developers.ashbyhq.com/docs/public-job-posting-api"""

    name = "ashby"

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        resp = self.http.get(f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}",
                             params={"includeCompensation": "true"})
        resp.raise_for_status()
        for j in resp.json().get("jobs", []):
            if j.get("isListed") is False:   # unlisted = meant for direct links only
                continue
            yield Posting(
                source=self.name, source_id=str(j.get("id") or j.get("jobUrl")), scope=company["slug"],
                title=j.get("title", ""), company=company.get("name", company["slug"]),
                location=j.get("location", ""), city=(j.get("location") or "").split(",")[0],
                remote={"Remote": True, "OnSite": False}.get(j.get("workplaceType", "")),
                url=j.get("jobUrl", ""), apply_url=j.get("applyUrl", ""),
                description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
                posted_at=_iso(j.get("publishedAt")),
                extra={"workplace_type": j.get("workplaceType"), "employment_type": j.get("employmentType"),
                       "compensation": (j.get("compensation") or {}).get("compensationTierSummary")},
            )


class PersonioAdapter(ATSAdapter):
    """https://developer.personio.de/docs/retrieving-open-job-positions (public XML feed)."""

    name = "personio"

    def fetch_company(self, company: dict[str, Any]) -> Iterator[Posting]:
        slug = company["slug"]
        resp = self.http.get(f"https://{slug}.jobs.personio.de/xml", params={"language": company.get("language", "de")})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for pos in root.iter("position"):
            pid = pos.findtext("id", "")
            descs = [
                f"{d.findtext('name', '')}\n{d.findtext('value', '')}"
                for d in pos.iter("jobDescription")
            ]
            office = pos.findtext("office", "")
            yield Posting(
                source=self.name, source_id=pid, scope=slug,
                title=pos.findtext("name", ""), company=pos.findtext("subcompany") or company.get("name", slug),
                location=office, city=office,
                url=f"https://{slug}.jobs.personio.de/job/{pid}",
                description="\n".join(descs),
                posted_at=_iso(pos.findtext("createdAt")),
                extra={"seniority": pos.findtext("seniority"), "schedule": pos.findtext("schedule"),
                       "keywords": pos.findtext("keywords")},
            )


# Map a careers-page URL to (ats, slug) without making any request.
URL_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ("personio", r"([\w-]+)\.jobs\.personio\.(?:de|com)"),
]


def detect_ats(url: str) -> tuple[str, str, bool] | None:
    """Return (ats, slug, eu) for a careers URL, or None."""
    import re

    for ats, pattern in URL_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return ats, m.group(1), ".eu." in url
    return None
