"""Builds adapters from config/sources.yaml, enforcing the terms gate."""

from __future__ import annotations

import logging
from typing import Any

from ..config import load_yaml
from ..http import PoliteClient
from .aggregators import AdzunaAdapter, ArbeitnowAdapter, BAJobsucheAdapter, JoobleAdapter
from .ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter, PersonioAdapter, RecruiteeAdapter, SmartRecruitersAdapter
from .base import SourceAdapter, SourceNotApproved, TermsCard, load_terms
from .mail_alerts import IndeedAlertsAdapter, LinkedInAlertsAdapter
from .manual import ManualAdapter

log = logging.getLogger(__name__)

ADAPTERS: dict[str, type[SourceAdapter]] = {
    cls.name: cls
    for cls in (
        BAJobsucheAdapter, AdzunaAdapter, ArbeitnowAdapter, JoobleAdapter,
        GreenhouseAdapter, LeverAdapter, RecruiteeAdapter, SmartRecruitersAdapter, AshbyAdapter, PersonioAdapter,
        LinkedInAlertsAdapter, IndeedAlertsAdapter, ManualAdapter,
    )
}

NO_HTTP = {"linkedin_alerts", "indeed_alerts", "manual"}


def source_config() -> dict[str, Any]:
    return load_yaml("sources.yaml")


def build(name: str, *, cfg: dict[str, Any] | None = None, http: PoliteClient | None = None,
          terms: TermsCard | None = None, enforce_terms: bool = True) -> SourceAdapter:
    if name not in ADAPTERS:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(ADAPTERS)}")
    if terms is None:
        terms = load_terms(name)
        # A local decision (config/sources.local.yaml, git-ignored) may approve a source
        # whose public card is not approved. It is logged so it is never silent.
        override = (source_config().get("terms_overrides") or {}).get(name)
        if override:
            log.warning("%s: terms status overridden locally to %r (config/sources.local.yaml)", name, override)
            terms.status = override
    if enforce_terms and terms.status != "approved":
        raise SourceNotApproved(
            f"{name}: terms card status is {terms.status!r} (docs/sources/{name}.md). "
            "Review the terms and set status: approved before collecting."
        )
    if terms.stale:
        log.warning("%s: terms were last checked on %s; re-check them", name, terms.checked)
    if cfg is None:
        cfg = (source_config().get("sources") or {}).get(name, {})
    if http is None and name not in NO_HTTP:
        http = PoliteClient(min_interval_s=terms.min_interval_s, max_requests=terms.max_requests_per_run)
    adapter = ADAPTERS[name](cfg, http, terms)
    adapter.verifiable = terms.verifiable and adapter.verifiable
    return adapter


def enabled_sources() -> list[str]:
    sources = source_config().get("sources") or {}
    return [name for name, cfg in sources.items() if (cfg or {}).get("enabled")]
