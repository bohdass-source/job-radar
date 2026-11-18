from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from job_radar.http import PoliteClient
from job_radar.models import Posting, utcnow
from job_radar.pipeline import collect as collect_mod
from job_radar.pipeline.clean import detect_language, html_to_text
from job_radar.pipeline.dedup import city_key, company_key, is_same_job, title_key
from job_radar.pipeline.freshness import compute_status
from job_radar.pipeline.relevance import is_relevant, is_relevant_title
from job_radar.pipeline.store import ingest, recompute_all
from job_radar.sources.ats import GreenhouseAdapter, detect_ats
from job_radar.sources.base import SourceNotApproved
from job_radar.sources.registry import build

from .conftest import FRESHNESS


def test_html_to_text_and_language():
    text = html_to_text("<h2>Deine Aufgaben</h2><ul><li>Du entwickelst ML-Modelle mit PyTorch</li></ul>"
                        "<p>Wir bieten ein tolles Team und sehr gute Kenntnisse sind von Vorteil.</p><script>x()</script>")
    assert "- Du entwickelst" in text and "x()" not in text
    assert detect_language(text) == "de"
    assert detect_language("You will build models with our team and you have strong skills in Python and the cloud") == "en"


@pytest.mark.parametrize("a,b", [
    ("Machine Learning Engineer (m/w/d)", "ML Engineer (f/m/x)"),
    ("Senior KI Entwickler*in", "Senior AI Entwickler"),
])
def test_title_key_normalises(a, b):
    assert title_key(a) == title_key(b)


def test_title_key_keeps_ai_ml():
    assert "ai" in title_key("AI/ML Engineer").split()


def test_company_and_city_keys():
    assert company_key("Siemens Healthineers AG") == company_key("Siemens Healthineers")
    assert company_key("Example GmbH & Co. KG") == "example"
    assert city_key("München, Bayern") == city_key("Munich") == "munich"


def test_is_same_job_requires_same_company_and_compatible_city():
    a = {"company_key": "acme", "title_key": "machine learning engineer", "city_key": "berlin", "remote": None}
    assert is_same_job(a, {**a, "city_key": ""})
    assert not is_same_job(a, {**a, "city_key": "munich"})
    assert is_same_job(a, {**a, "city_key": "munich", "remote": True})
    assert not is_same_job(a, {**a, "company_key": "other"})


def test_relevance():
    assert is_relevant_title("ML-Engineer (m/w/d)")
    assert is_relevant_title("Werkstudent KI-Entwicklung")
    assert not is_relevant_title("Sales Manager AI Solutions")
    assert not is_relevant_title("Mainframe Developer")
    assert is_relevant("Software Engineer", "We train models with PyTorch and serve them with MLflow")


def _p(source, sid, title="Machine Learning Engineer (m/w/d)", company="Acme GmbH", city="Berlin", **kw):
    return Posting(source=source, source_id=sid, title=title, company=company, city=city,
                   description=kw.pop("description", "Build ML models with PyTorch."), **kw)


def test_cross_source_merge_and_idempotent_rerun(db):
    now = utcnow()
    assert ingest(db, _p("greenhouse", "1", scope="acme"), now=now, verifiable=True, freshness_cfg=FRESHNESS) == "new_job"
    assert ingest(db, _p("adzuna", "a9", title="ML Engineer", company="ACME", city="Berlin"),
                  now=now, verifiable=True, freshness_cfg=FRESHNESS) == "merged"
    assert ingest(db, _p("linkedin_alerts", "777", title="Machine Learning Engineer", company="Acme", city=""),
                  now=now, verifiable=False, freshness_cfg=FRESHNESS) == "merged"
    assert db.jobs.count_documents({}) == 1
    job = db.jobs.find_one({})
    assert sorted(r["source"] for r in job["sources"]) == ["adzuna", "greenhouse", "linkedin_alerts"]
    assert job["status"] == "open"
    # re-run: nothing new
    for _ in range(2):
        assert ingest(db, _p("greenhouse", "1", scope="acme"), now=now, verifiable=True, freshness_cfg=FRESHNESS) == "seen"
    assert db.jobs.count_documents({}) == 1 and db.postings.count_documents({}) == 3


def test_full_description_replaces_snippet(db):
    now = utcnow()
    ingest(db, _p("adzuna", "a1", description="short snippet", description_is_snippet=True), now=now,
           verifiable=True, freshness_cfg=FRESHNESS)
    ingest(db, _p("greenhouse", "g1", description="long full text " * 20, scope="acme"), now=now, verifiable=True,
           freshness_cfg=FRESHNESS)
    job = db.jobs.find_one({})
    assert not job["description_is_snippet"] and job["description"].startswith("long full text")


def test_freshness_rules():
    now = utcnow()
    ref = {"source": "greenhouse", "verifiable": True, "gone": False, "last_seen": now}
    assert compute_status({"sources": [ref], "first_seen": now}, FRESHNESS, now)[0] == "open"
    assert compute_status({"sources": [{**ref, "gone": True}], "first_seen": now}, FRESHNESS, now)[0] == "closed"
    alert = {"source": "linkedin_alerts", "verifiable": False, "gone": False, "last_seen": now - timedelta(days=30)}
    assert compute_status({"sources": [alert], "first_seen": now - timedelta(days=30)}, FRESHNESS, now)[0] == "expired"
    assert compute_status({"sources": [{**alert, "last_seen": now}], "first_seen": now}, FRESHNESS, now)[0] == "unverified"
    old_agg = {"source": "adzuna", "verifiable": True, "gone": False, "last_seen": now}
    assert compute_status({"sources": [old_agg], "first_seen": now - timedelta(days=120)}, FRESHNESS, now)[0] == "expired"
    assert compute_status({"sources": [ref], "first_seen": now - timedelta(days=120)}, FRESHNESS, now)[0] == "open"
    manual = {"source": "manual", "verifiable": False, "last_seen": now}
    job = {"sources": [manual], "first_seen": now, "valid_through": (now - timedelta(days=1)).isoformat()}
    assert compute_status(job, FRESHNESS, now)[0] == "expired"


def _greenhouse_transport(jobs_by_call):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            jobs = jobs_by_call[min(calls["n"], len(jobs_by_call) - 1)]
            calls["n"] += 1
            return httpx.Response(200, json={"jobs": jobs})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _gh_job(i, title="Machine Learning Engineer"):
    return {"id": i, "title": title, "updated_at": "2026-11-01T10:00:00Z", "location": {"name": "Berlin, Germany"},
            "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
            "content": "&lt;p&gt;We use PyTorch and Kubernetes.&lt;/p&gt;"}


def test_job_closes_after_missing_from_complete_listings(db, terms, monkeypatch):
    monkeypatch.setattr(collect_mod, "freshness_cfg", lambda: FRESHNESS)
    feeds = [[_gh_job(1), _gh_job(2, "Data Scientist")], [_gh_job(2, "Data Scientist")], [_gh_job(2, "Data Scientist")]]
    transport = _greenhouse_transport(feeds)
    cfg = {"companies": [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]}
    for _ in range(3):
        http = PoliteClient(min_interval_s=0, transport=transport, sleep=lambda s: None)
        adapter = GreenhouseAdapter(cfg, http, terms())
        stats = collect_mod.run_adapter(db, adapter)
        assert stats.completed_scopes == ["acme"]
    statuses = {j["title"]: j["status"] for j in db.jobs.find({})}
    assert statuses == {"Machine Learning Engineer": "closed", "Data Scientist": "open"}
    assert db.jobs.count_documents({}) == 2


def test_failed_listing_does_not_count_as_miss(db, terms, monkeypatch):
    monkeypatch.setattr(collect_mod, "freshness_cfg", lambda: FRESHNESS)
    ok = _greenhouse_transport([[_gh_job(1)]])
    broken = httpx.MockTransport(lambda r: httpx.Response(503))
    cfg = {"companies": [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]}
    collect_mod.run_adapter(db, GreenhouseAdapter(cfg, PoliteClient(min_interval_s=0, transport=ok, sleep=lambda s: None), terms()))
    for _ in range(3):
        http = PoliteClient(min_interval_s=0, transport=broken, sleep=lambda s: None, max_retries=0)
        stats = collect_mod.run_adapter(db, GreenhouseAdapter(cfg, http, terms()))
        assert stats.completed_scopes == [] and stats.errors
    assert db.jobs.find_one({})["status"] == "open"


def test_terms_gate_blocks_unapproved_sources(terms):
    with pytest.raises(SourceNotApproved):
        build("greenhouse", terms=terms(status="needs_review"), cfg={})


def test_real_terms_cards_load():
    from job_radar.sources.base import load_terms
    from job_radar.sources.registry import ADAPTERS

    for name in ADAPTERS:
        card = load_terms(name)
        assert card.name == name and card.checked
    assert all(load_terms(n).status in ("approved", "needs_review", "blocked") for n in ADAPTERS)


def test_germany_filter():
    from job_radar.pipeline.relevance import is_germany

    assert is_germany("Berlin") and is_germany("Remote job") and is_germany("")
    assert not is_germany("9th arrondissement of Paris, 75009, Paris")
    assert not is_germany("Zürich, Switzerland") and not is_germany("Berlin", country="FR")
    assert is_germany("Berlin; London")
    assert not is_relevant_title("Social Media & AI Manager") and is_relevant_title("Marketing Data Scientist")


def test_polite_client_waits_and_backs_off():
    slept = []
    clock = {"t": 0.0}
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})])
    client = PoliteClient(min_interval_s=2, transport=httpx.MockTransport(lambda r: next(responses)),
                          sleep=slept.append, clock=lambda: clock["t"])
    assert client.get("https://example.org/a").status_code == 200
    assert 7 in slept and 2 in slept   # Retry-After honoured, then the per-host pause
    assert client.requests == 2


def test_detect_ats():
    assert detect_ats("https://jobs.ashbyhq.com/acme-ai") == ("ashby", "acme-ai", False)
    assert detect_ats("https://jobs.eu.lever.co/acme") == ("lever", "acme", True)
    assert detect_ats("https://acme.jobs.personio.de/job/123") == ("personio", "acme", False)
    assert detect_ats("https://boards.greenhouse.io/acme") == ("greenhouse", "acme", False)
    assert detect_ats("https://example.org/careers") is None


def test_recompute_all_counts(db):
    ingest(db, _p("greenhouse", "1", scope="acme"), now=utcnow(), verifiable=True, freshness_cfg=FRESHNESS)
    assert recompute_all(db, FRESHNESS, utcnow()) == {"open": 1}


def test_ba_adapter_real_shape(db, terms):
    from job_radar.sources.aggregators import BAJobsucheAdapter

    item = {"stellenangebotsTitel": "Machine Learning Engineer (w/m/d)", "firma": "Brunel GmbH",
            "referenznummer": "12362-TR249390_4973CS-S", "datumErsteVeroeffentlichung": "2026-10-19",
            "stellenlokationen": [{"adresse": {"ort": "Braunschweig", "region": "NIEDERSACHSEN"}}],
            "homeofficemoeglich": True, "homeofficetyp": "NACH_VEREINBARUNG",
            "gehaltsspanneVon": 70000, "gehaltsspanneBis": 85000}
    other = {**item, "stellenangebotsTitel": "Lagerhelfer", "referenznummer": "x-1"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pc/v6/jobs" in request.url.path:
            return httpx.Response(200, json={"ergebnisliste": [item, other], "maxErgebnisse": 2})
        if "/pc/v4/jobdetails/" in request.url.path:
            assert "%2F" in request.url.raw_path.decode() or "/" not in request.url.path.split("jobdetails/")[1]
            return httpx.Response(200, json={"stellenangebotsBeschreibung": "Wir nutzen PyTorch."})
        return httpx.Response(404)

    http = PoliteClient(min_interval_s=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    adapter = BAJobsucheAdapter({"queries": ["ML"]}, http, terms("ba_jobsuche"))
    posts = list(adapter.fetch())
    assert adapter.completed_scopes == {"ML"}
    ml = posts[0]
    assert (ml.title, ml.company, ml.city, ml.salary_min) == ("Machine Learning Engineer (w/m/d)", "Brunel GmbH", "Braunschweig", 70000)
    assert ml.description == "Wir nutzen PyTorch." and not ml.description_is_snippet
    assert posts[1].description == "" and http.requests == 2    # no detail call for the irrelevant title


def test_ba_adapter_stops_on_403(db, terms):
    from job_radar.sources.aggregators import BAJobsucheAdapter

    http = PoliteClient(min_interval_s=0, transport=httpx.MockTransport(lambda r: httpx.Response(403, text="captcha")),
                        sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="no bypass"):
        list(BAJobsucheAdapter({"queries": ["ML"]}, http, terms("ba_jobsuche")).fetch())


def test_rerun_without_text_keeps_stored_description(db):
    now = utcnow()
    ingest(db, _p("ba_jobsuche", "r1", description="Full text with PyTorch"), now=now, verifiable=True, freshness_cfg=FRESHNESS)
    ingest(db, _p("ba_jobsuche", "r1", description="", description_is_snippet=True), now=now, verifiable=True,
           freshness_cfg=FRESHNESS)
    post = db.postings.find_one({"source_id": "r1"})
    assert post["description"] == "Full text with PyTorch" and post["description_is_snippet"] is False


def test_generic_ai_titles_need_a_technical_role():
    assert is_relevant_title("KI-Engineer (m/w/d) - Hybrid") and is_relevant_title("Werkstudent KI-Entwicklung")
    assert not is_relevant_title("Rechtsanwalt KI- und Datenrecht (m/w/d)")
    assert not is_relevant_title("Manager KI-gestützte Prozessoptimierung")
    assert not is_relevant("AI Associate", "Sales role") and is_relevant("AI Associate", "We build RAG systems")


def test_local_config_deep_merge():
    from job_radar.config import _deep_merge

    base = {"sources": {"ba_jobsuche": {"enabled": False, "queries": ["ML"]}, "arbeitnow": {"enabled": True}}}
    out = _deep_merge(base, {"sources": {"ba_jobsuche": {"enabled": True}}, "terms_overrides": {"ba_jobsuche": "approved"}})
    assert out["sources"]["ba_jobsuche"] == {"enabled": True, "queries": ["ML"]}
    assert out["sources"]["arbeitnow"]["enabled"] and out["terms_overrides"]["ba_jobsuche"] == "approved"


def test_consultant_must_be_a_whole_word():
    assert not is_relevant_title("Steuerberater:in KI affin & remote")
    assert is_relevant_title("KI-Berater (m/w/d)") and is_relevant_title("AI Consultant")


def test_arbeitnow_country_footer(terms):
    from job_radar.sources.aggregators import ArbeitnowAdapter

    def item(slug, country):
        return {"slug": slug, "title": "ML Engineer", "company_name": "X", "location": "Remote", "remote": True,
                "url": "u", "created_at": 1794826918, "description": f"Python. Find Jobs in {country} on Arbeitnow"}
    page = {"data": [item("a", "Germany"), item("b", "United Kingdom")], "links": {"next": None}}
    http = PoliteClient(min_interval_s=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page)),
                        sleep=lambda s: None)
    posts = list(ArbeitnowAdapter({}, http, terms("arbeitnow")).fetch())
    from job_radar.pipeline.relevance import is_germany

    assert [is_germany(p.location, p.city, p.country) for p in posts] == [True, False]


def test_retrieval_qrels_import(tmp_path, monkeypatch):
    import json

    from job_radar.bench import retrieval

    monkeypatch.setattr(retrieval, "private_path", lambda *p: tmp_path.joinpath(*p))
    (tmp_path / "bench").mkdir()
    rows = [{"qid": "q01", "query": "LLM", "job_id": j} for j in ("a", "b", "c")]
    (tmp_path / "bench" / "pool.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    judged = tmp_path / "rel.json"
    judged.write_text(json.dumps({"q01": ["b"]}))
    out = json.loads(retrieval.import_judgments(judged, "test").read_text())
    assert out["q01"]["judged"] == ["a", "b", "c"] and out["q01"]["relevant"] == ["b"]
    assert retrieval.recall_at(["b", "x"], {"b"}, 5) == 1.0 and retrieval.mrr_at(["x", "b"], {"b"}, 10) == 0.5
    assert abs(retrieval.ndcg_at(["x", "b"], {"b"}, 10) - 1 / __import__("math").log2(3)) < 1e-9
