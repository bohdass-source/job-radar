"""Search-API sources: Adzuna, Arbeitnow, Jooble and the (blocked) BA Jobsuche API."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any, Iterator

from ..config import settings
from ..models import Posting
from .base import CheckResult, SourceAdapter


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class AdzunaAdapter(SourceAdapter):
    """https://developer.adzuna.com/docs/search — returns only a description snippet."""

    name = "adzuna"
    BASE = "https://api.adzuna.com/v1/api/jobs/de/search/{page}"

    def fetch(self) -> Iterator[Posting]:
        s = settings()
        if not (s.adzuna_app_id and s.adzuna_app_key):
            raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY are not set in .env")
        per_page = int(self.cfg.get("results_per_page", 50))
        max_pages = int(self.cfg.get("max_pages", 20))
        for query in self.cfg.get("queries", []):
            for page in range(1, max_pages + 1):
                resp = self.http.get(
                    self.BASE.format(page=page),
                    params={
                        "app_id": s.adzuna_app_id,
                        "app_key": s.adzuna_app_key,
                        "what": query,
                        "results_per_page": per_page,
                        "max_days_old": self.cfg.get("max_days_old", 30),
                        "content-type": "application/json",
                    },
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                for item in results:
                    posting = self._map(item)
                    posting.scope = query
                    yield posting
                if len(results) < per_page:
                    self.completed_scopes.add(query)
                    break

    def _map(self, x: dict[str, Any]) -> Posting:
        loc = x.get("location") or {}
        area = loc.get("area") or []
        predicted = str(x.get("salary_is_predicted", "0")) == "1"
        return Posting(
            source=self.name,
            source_id=str(x["id"]),
            title=x.get("title", ""),
            company=(x.get("company") or {}).get("display_name", ""),
            location=loc.get("display_name", ""),
            city=area[-1] if len(area) >= 3 else "",
            url=x.get("redirect_url", ""),
            description=x.get("description", ""),
            description_is_snippet=True,
            posted_at=_iso(x.get("created")),
            # Predicted salaries are Adzuna's estimate, not the employer's figure.
            salary_min=None if predicted else x.get("salary_min"),
            salary_max=None if predicted else x.get("salary_max"),
            salary_currency=None if predicted or not x.get("salary_min") else "EUR",
            salary_period="year" if not predicted and x.get("salary_min") else None,
            extra={"contract_time": x.get("contract_time"), "category": (x.get("category") or {}).get("tag"),
                   "salary_predicted": predicted},
        )


class ArbeitnowAdapter(SourceAdapter):
    """https://www.arbeitnow.com/api/job-board-api — no key, full HTML descriptions, 300 per page."""

    name = "arbeitnow"
    URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self) -> Iterator[Posting]:
        max_pages = int(self.cfg.get("max_pages", 10))
        for page in range(1, max_pages + 1):
            resp = self.http.get(self.URL, params={"page": page})
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("data", []):
                # Arbeitnow lists all of Europe; each ad ends with "... Jobs in <Country> on Arbeitnow".
                m = re.search(r"Jobs in ([A-Z][A-Za-z ]+?) on Arbeitnow", item.get("description", ""))
                country = "DE" if not m or m.group(1) == "Germany" else m.group(1)
                yield Posting(
                    source=self.name,
                    source_id=item["slug"],
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    location=item.get("location", ""),
                    city=item.get("location", ""),
                    country=country,
                    remote=item.get("remote"),
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    posted_at=datetime.fromtimestamp(item["created_at"], tz=timezone.utc)
                    if item.get("created_at") else None,
                    extra={"tags": item.get("tags", []), "job_types": item.get("job_types", [])},
                )
            if not (data.get("links") or {}).get("next"):
                self.completed_scopes.add("all")
                return


class JoobleAdapter(SourceAdapter):
    """Jooble REST API. The endpoint shape below comes from Jooble's code samples and
    must be confirmed against the docs you receive with the key (see the terms card)."""

    name = "jooble"

    def fetch(self) -> Iterator[Posting]:
        key = settings().jooble_api_key
        if not key:
            raise RuntimeError("JOOBLE_API_KEY is not set in .env")
        url = f"https://de.jooble.org/api/{key}"
        max_pages = int(self.cfg.get("max_pages", 10))
        for query in self.cfg.get("queries", []):
            for page in range(1, max_pages + 1):
                resp = self.http.post(url, json={"keywords": query, "location": "Deutschland", "page": str(page)})
                resp.raise_for_status()
                jobs = resp.json().get("jobs", [])
                for j in jobs:
                    yield Posting(
                        source=self.name,
                        source_id=str(j.get("id") or j.get("link")),
                        title=j.get("title", ""),
                        company=j.get("company", ""),
                        location=j.get("location", ""),
                        city=(j.get("location") or "").split(",")[0],
                        url=j.get("link", ""),
                        description=j.get("snippet", ""),
                        description_is_snippet=True,
                        posted_at=_iso(j.get("updated")),
                        scope=query,
                        extra={"salary_text": j.get("salary"), "via": j.get("source"), "type": j.get("type")},
                    )
                if not jobs:
                    self.completed_scopes.add(query)
                    break


class BAJobsucheAdapter(SourceAdapter):
    """Bundesagentur für Arbeit Jobsuche endpoint (documented by bundesAPI; the BA itself
    offers no official API). Approved by the project owner despite Nutzungsbedingungen
    §2a(3) — see docs/sources/ba_jobsuche.md for the conditions this adapter keeps.

    List: GET /pc/v6/jobs  -> "ergebnisliste" (no description).
    Detail: GET /pc/v4/jobdetails/{base64(referenznummer)} -> "stellenangebotsBeschreibung".
    Details are fetched only for AI/ML-relevant titles that have no stored text yet, so
    after the first run a daily run costs little more than the list pages.
    A 403 or any non-JSON answer stops the run: no captcha or bot-protection bypass.
    """

    name = "ba_jobsuche"
    BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
    HEADERS = {"X-API-Key": "jobboerse-jobsuche"}
    SIZE = 50

    def _json(self, resp: Any) -> dict[str, Any]:
        if resp.status_code == 403 or "json" not in resp.headers.get("content-type", ""):
            raise RuntimeError(f"BA answered {resp.status_code} {resp.headers.get('content-type')}; stopping (no bypass)")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _detail_path(refnr: str) -> str:
        from urllib.parse import quote

        return quote(base64.b64encode(refnr.encode()).decode(), safe="")

    def _has_text(self, refnr: str) -> bool:
        from ..db import get_db

        return get_db().postings.find_one({"source": self.name, "source_id": refnr, "description_is_snippet": False},
                                          {"_id": 1}) is not None

    def fetch(self) -> Iterator[Posting]:
        from ..pipeline.relevance import is_relevant_title

        max_pages = int(self.cfg.get("max_pages", 40))
        seen: set[str] = set()
        # Each query is its own scope: one over-broad query hitting the page cap
        # must not stop the others from confirming that their jobs disappeared.
        for query in self.cfg.get("queries", []):
            for page in range(1, max_pages + 1):
                data = self._json(self.http.get(
                    f"{self.BASE}/pc/v6/jobs", headers=self.HEADERS,
                    params={"was": query, "angebotsart": 1, "page": page, "size": self.SIZE,
                            "veroeffentlichtseit": self.cfg.get("max_days_old", 60)},
                ))
                items = data.get("ergebnisliste") or []
                for x in items:
                    refnr = x.get("referenznummer") or x.get("refnr")
                    if not refnr or refnr in seen:
                        continue
                    seen.add(refnr)
                    posting = self._posting(x, refnr, is_relevant_title)
                    posting.scope = query
                    yield posting
                total = int(data.get("maxErgebnisse") or 0)
                if len(items) < self.SIZE or page * self.SIZE >= total:
                    self.completed_scopes.add(query)
                    break

    def _posting(self, x: dict[str, Any], refnr: str, is_relevant_title: Any) -> Posting:
        title = x.get("stellenangebotsTitel") or x.get("hauptberuf", "")
        first_loc = (x.get("stellenlokationen") or [{}])[0]
        loc = first_loc.get("adresse") or {}
        description, snippet = "", True
        if is_relevant_title(title) and not self._has_text(refnr):
            detail = self.http.get(f"{self.BASE}/pc/v4/jobdetails/{self._detail_path(refnr)}", headers=self.HEADERS)
            if detail.status_code == 200 and "json" in detail.headers.get("content-type", ""):
                description = detail.json().get("stellenangebotsBeschreibung") or ""
                snippet = not description
            elif detail.status_code == 403:
                raise RuntimeError("BA detail endpoint answered 403; stopping (no bypass)")
        homeoffice = x.get("homeofficemoeglich")
        salary_lo, salary_hi = x.get("gehaltsspanneVon"), x.get("gehaltsspanneBis")
        return Posting(
            source=self.name, source_id=refnr, title=title, company=x.get("firma", ""),
            city=loc.get("ort", ""), location=", ".join(filter(None, [loc.get("ort"), loc.get("region")])),
            # The BA also lists Austrian and Swiss jobs; "land" says which country.
            country="DE" if (loc.get("land") or "DEUTSCHLAND").upper() == "DEUTSCHLAND" else loc["land"],
            remote=None if homeoffice is None else (True if x.get("homeofficetyp") == "AUSSCHLIESSLICH" else None),
            url=x.get("externeURL") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
            description=description, description_is_snippet=snippet,
            posted_at=_iso(x.get("datumErsteVeroeffentlichung")),
            salary_min=salary_lo, salary_max=salary_hi, salary_currency="EUR" if salary_lo else None,
            salary_period="year" if salary_lo else None,
            extra={"homeoffice": homeoffice, "homeoffice_type": x.get("homeofficetyp"),
                   "changed": x.get("aenderungsdatum"), "profession": x.get("hauptberuf"),
                   "lat": first_loc.get("breite"), "lon": first_loc.get("laenge"),
                   "fulltime": x.get("arbeitszeitVollzeit"),
                   "parttime": any(x.get(k) for k in ("arbeitszeitTeilzeitVormittag", "arbeitszeitTeilzeitNachmittag",
                                                       "arbeitszeitTeilzeitAbend", "arbeitszeitTeilzeitFlexibel")),
                   "contract": x.get("vertragsdauer"), "limited_months": x.get("befristungInMonaten"),
                   "career_changer_ok": x.get("quereinstiegGeeignet"),
                   "minor_job": x.get("istGeringfuegigeBeschaeftigung")},
        )

    def check(self, posting: dict[str, Any]) -> CheckResult:
        resp = self.http.get(f"{self.BASE}/pc/v4/jobdetails/{self._detail_path(posting['source_id'])}",
                             headers=self.HEADERS)
        if resp.status_code == 404:
            return "closed"
        return "open" if resp.status_code == 200 else "unknown"
