"""Core data models shared by adapters, pipeline and matching."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class JobStatus(StrEnum):
    OPEN = "open"              # seen recently in a source that can confirm it
    UNVERIFIED = "unverified"  # only from mail alerts / manual pages; cannot be re-checked automatically
    CLOSED = "closed"          # disappeared from every verifiable source, or a detail check said 404
    EXPIRED = "expired"        # unverified for too long, or older than max age


class Posting(BaseModel):
    """One ad as one source delivers it, after the adapter mapped its fields."""

    source: str
    source_id: str
    title: str
    company: str = ""
    location: str = ""
    city: str = ""
    country: str = "DE"
    remote: bool | None = None
    url: str = ""
    apply_url: str = ""
    description: str = ""          # plain text (adapters may hand over HTML; clean() strips it)
    description_is_snippet: bool = False
    posted_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None   # year | month | hour
    #: Which listing of the source this came from (a company slug for ATS feeds,
    #: "all" for search APIs). Freshness counts misses per completed scope.
    scope: str = "all"
    extra: dict = Field(default_factory=dict)


class SourceRef(BaseModel):
    source: str
    source_id: str
    url: str = ""
    scope: str = "all"
    first_seen: datetime
    last_seen: datetime
    missed_runs: int = 0
    gone: bool = False          # this source no longer lists it
    verifiable: bool = True


class CollectStats(BaseModel):
    source: str
    fetched: int = 0
    relevant: int = 0
    new_postings: int = 0
    new_jobs: int = 0
    merged_into_existing: int = 0
    completed_scopes: list[str] = Field(default_factory=list)  # listings read to the end
    requests: int = 0
    errors: list[str] = Field(default_factory=list)
