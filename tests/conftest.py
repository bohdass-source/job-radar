from __future__ import annotations

from datetime import date

import mongomock
import pytest

from job_radar import db as dbmod
from job_radar.sources.base import TermsCard


@pytest.fixture
def db():
    client = mongomock.MongoClient(tz_aware=True)
    database = client["job_radar_test"]
    dbmod.set_db(database)
    yield database
    dbmod._db = None


@pytest.fixture
def terms():
    def make(name: str = "greenhouse", status: str = "approved", verifiable: bool = True) -> TermsCard:
        return TermsCard(name=name, title=name, terms_urls=[], checked=date.today(), rate_limit="test",
                         min_interval_s=0, max_requests_per_run=1000, status=status, verifiable=verifiable)
    return make


FRESHNESS = {"missed_runs_to_close": 2, "unverified_ttl_days": 21, "max_age_days": 90}
