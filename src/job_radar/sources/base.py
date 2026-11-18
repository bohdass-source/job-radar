"""Common adapter interface and the terms card every adapter must have.

Each adapter has a card in docs/sources/<name>.md with YAML front matter:
terms URLs, the date the terms were checked, the request limit, and a status.
`collect` refuses to run an adapter whose card status is not `approved`,
so a source cannot be switched on without its terms having been reviewed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator, Literal

import yaml

from ..config import DOCS_SOURCES_DIR
from ..http import PoliteClient
from ..models import Posting

TermsStatus = Literal["approved", "needs_review", "blocked"]
CheckResult = Literal["open", "closed", "unknown"]

RECHECK_AFTER_DAYS = 180


class SourceNotApproved(RuntimeError):
    pass


@dataclass
class TermsCard:
    name: str
    title: str
    terms_urls: list[str]
    checked: date
    rate_limit: str
    min_interval_s: float
    max_requests_per_run: int
    status: TermsStatus
    verifiable: bool
    body: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def stale(self) -> bool:
        return (date.today() - self.checked).days > RECHECK_AFTER_DAYS


def load_terms(name: str) -> TermsCard:
    path = DOCS_SOURCES_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path} has no front matter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    known = {
        "name", "title", "terms_urls", "checked", "rate_limit", "min_interval_s",
        "max_requests_per_run", "status", "verifiable",
    }
    return TermsCard(
        name=meta["name"],
        title=meta["title"],
        terms_urls=list(meta.get("terms_urls") or []),
        checked=meta["checked"] if isinstance(meta["checked"], date) else date.fromisoformat(str(meta["checked"])),
        rate_limit=str(meta.get("rate_limit", "unknown")),
        min_interval_s=float(meta.get("min_interval_s", 2.0)),
        max_requests_per_run=int(meta.get("max_requests_per_run", 200)),
        status=meta.get("status", "needs_review"),
        verifiable=bool(meta.get("verifiable", True)),
        body=body.strip(),
        extra={k: v for k, v in meta.items() if k not in known},
    )


class SourceAdapter(ABC):
    """One source of job ads.

    fetch() yields Postings and adds a posting's scope to self.completed_scopes
    only when it read that listing to the end. Freshness relies on this: a job
    missing from a *completed* listing counts as a miss; a job missing from a
    listing that stopped early (budget, error, page cap) does not.
    """

    name: str = ""
    #: Can this source confirm that an ad is still open? Mail alerts and
    #: manually sent pages cannot, because we never visit those sites.
    verifiable: bool = True

    def __init__(self, cfg: dict[str, Any], http: PoliteClient | None, terms: TermsCard) -> None:
        self.cfg = cfg or {}
        self.http = http
        self.terms = terms
        self.completed_scopes: set[str] = set()

    @abstractmethod
    def fetch(self) -> Iterator[Posting]: ...

    def check(self, posting: dict[str, Any]) -> CheckResult:
        """Re-check one stored posting. Override when the source has a detail endpoint."""
        return "unknown"
