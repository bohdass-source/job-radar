from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from job_radar.extract.extractor import extract_one, to_document
from job_radar.extract.llm import LLMClient, LLMInvalidOutput
from job_radar.extract.rules import extract_rules
from job_radar.extract.schema import JobExtraction
from job_radar.match.interview import run_interview
from job_radar.match.matcher import evaluate, match_all
from job_radar.match.profile import Profile, profile_from_text
from job_radar.models import Posting, utcnow
from job_radar.pipeline.store import ingest
from job_radar.report.aggregates import market_stats, source_table
from job_radar.report.html import render_private, render_public, safe_url
from job_radar.search.chunkers import fixed_chunks, section_chunks, sentence_chunks
from job_radar.search.embed import HashingEmbedder
from job_radar.search.index import VectorIndex
from job_radar.skills import canonical, find_in_text
from job_radar.sources.mail_alerts import LinkedInAlertsAdapter, parse_alert_html
from job_radar.sources.manual import posting_from_page
from job_radar.tracker import list_applications, set_status

from .conftest import FRESHNESS

AD_DE = """Deine Aufgaben
- Du entwickelst LLM-Anwendungen mit RAG und PyTorch
- Du betreibst Modelle auf Kubernetes
Dein Profil
- Mindestens 3 Jahre Erfahrung mit Python und Docker
- Sehr gute Deutschkenntnisse und gute Englischkenntnisse
Von Vorteil
- Erfahrung mit AWS
Wir bieten
- Hybrides Arbeiten (2 Tage Homeoffice), 65.000 - 80.000 € im Jahr"""


def test_skill_synonyms():
    assert canonical("Torch") == "pytorch"
    assert canonical("Large Language Models") == "llm"
    assert canonical("PyTorch (advanced)") == "pytorch"
    assert canonical("TF") == "tensorflow"
    assert "go" not in find_in_text("Let's go to the office")       # short ambiguous aliases are not scanned
    assert set(find_in_text("Erfahrung mit K8s, HuggingFace und sklearn")) == {"kubernetes", "hugging face", "scikit-learn"}


def test_rules_extractor():
    ext = extract_rules("Senior Machine Learning Engineer (m/w/d)", AD_DE, "Berlin")
    assert ext.seniority == "senior"
    assert ext.german_level == "C1" and ext.english_level == "B2"
    assert ext.remote == "hybrid"
    assert ext.salary and ext.salary.min == 65000 and ext.salary.max == 80000
    assert ext.years_experience_min == 3
    assert {"llm", "rag", "pytorch", "kubernetes", "python", "docker"} <= set(ext.skills_required)
    assert ext.skills_nice_to_have == ["aws"]


def test_rules_optional_german():
    ext = extract_rules("AI Engineer", "Fluent English required. German is a plus.")
    assert ext.german_level == "none" and ext.english_level == "C1"


def _gateway(answers):
    it = iter(answers)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": next(it)}}]})
    return httpx.MockTransport(handler), seen


VALID = {"seniority": "mid", "skills_required": ["PyTorch", "Large Language Models", "Rust-ish thing"],
         "skills_nice_to_have": ["AWS"], "german_level": "B2", "english_level": "C1", "remote": "hybrid",
         "salary": None, "city": "München", "years_experience_min": 2, "visa_sponsorship": None}


def test_llm_retry_on_invalid_json():
    transport, seen = _gateway(["not json at all", json.dumps({**VALID, "seniority": "wizard"}), json.dumps(VALID)])
    llm = LLMClient(base_url="http://gw", api_key="k", model="m", transport=transport)
    ext = llm.complete_json("sys", "user", JobExtraction)
    assert ext.seniority == "mid" and len(seen) == 3
    # the validation error was fed back to the model
    assert "not valid for the schema" in seen[2]["messages"][-1]["content"]


def test_llm_gives_up_after_max_attempts():
    transport, _ = _gateway(["{}", "{}", "{}"])
    llm = LLMClient(base_url="http://gw", api_key="", model="m", transport=transport)
    with pytest.raises(LLMInvalidOutput) as err:
        llm.complete_json("sys", "user", JobExtraction)
    assert len(err.value.attempts) == 3


def test_extract_one_normalises_llm_skills_and_falls_back():
    transport, _ = _gateway([json.dumps(VALID)])
    llm = LLMClient(base_url="http://gw", api_key="", model="m", transport=transport)
    doc, method = extract_one({"title": "ML Engineer", "description": "x"}, llm)
    assert method == "llm"
    assert doc["skills_required"] == ["pytorch", "llm"] and doc["skills_other"] == ["Rust-ish thing"]
    doc, method = extract_one({"title": "ML Engineer", "description": AD_DE}, LLMClient(base_url=""))
    assert method == "rules" and "pytorch" in doc["skills_required"]


def test_chunkers():
    s = section_chunks("j1", "ML Engineer", AD_DE, company="Acme")
    assert len(s) == 4 and all(c.text.startswith("ML Engineer @ Acme") for c in s)
    assert any("| Dein Profil" in c.text for c in s)
    words = " ".join(f"w{i}" for i in range(300))
    f = fixed_chunks("j1", "T", words, size=120, overlap=30)
    # 301 words (title + 300), step 90: windows start at 0, 90, 180, 270; the title counts as one word
    assert len(f) == 4 and f[1].text.split()[0] == "w89" and f[-1].text.split()[-1] == "w299"
    sents = sentence_chunks("j1", "T", "Satz eins ist hier. " * 60, budget=50)
    assert all(len(c.text.split()) <= 54 for c in sents) and len(sents) > 3


def test_vector_index_roundtrip_and_filter(tmp_path):
    emb = HashingEmbedder()
    jobs = [{"job_id": "a", "title": "Computer Vision Engineer", "description": "object detection opencv cameras"},
            {"job_id": "b", "title": "NLP Engineer", "description": "transformers text classification llm"},
            {"job_id": "c", "title": "Closed NLP job", "description": "transformers text llm"}]
    idx = VectorIndex.build(jobs, emb, "section")
    idx.save(tmp_path)
    idx2 = VectorIndex.load(tmp_path)
    np.testing.assert_allclose(idx.vectors, idx2.vectors)
    hits = idx2.search(emb.encode(["llm text transformers"], "query")[0], k=3, allowed_jobs={"a", "b"})
    assert [h.job_id for h in hits][0] == "b" and "c" not in {h.job_id for h in hits}


def _job(db, sid="1", title="Machine Learning Engineer", text=AD_DE, city="Berlin", source="greenhouse", verifiable=True):
    ingest(db, Posting(source=source, source_id=sid, title=title, company=f"Co{sid}", city=city, description=text,
                       scope="x"), now=utcnow(), verifiable=verifiable, freshness_cfg=FRESHNESS)
    return db.jobs.find_one({"company": f"Co{sid}"})


def _profile(**kw):
    base = dict(skills={"python": 3, "pytorch": 2, "llm": 2, "rag": 2, "kubernetes": 1, "docker": 2,
                        "machine learning": 2},
                seniority="senior", years_experience=4, german_level="C1", english_level="C1",
                cities=["Berlin"], learning=["aws"])
    base.update(kw)
    return Profile(**base)


def test_three_buckets(db):
    job = _job(db)
    assert evaluate(job, _profile()).bucket == "apply"
    res = evaluate(job, _profile(skills={"python": 3, "pytorch": 2, "llm": 2, "docker": 2, "machine learning": 2}))
    assert res.bucket == "stretch" and set(res.missing_required) == {"rag", "kubernetes"}
    assert evaluate(job, _profile(german_level="A2")).bucket == "reject"          # C1 required, A2 -> 2+ steps
    assert evaluate(job, _profile(german_level="B2")).bucket == "stretch"         # one step below
    assert evaluate(job, _profile(cities=["Hamburg"])).bucket == "reject"          # hybrid in Berlin
    assert evaluate(job, _profile(cities=["Hamburg"], relocate=True)).bucket == "apply"
    senior_job = _job(db, sid="9", title="Senior Machine Learning Engineer")
    assert evaluate(senior_job, _profile(seniority="junior")).bucket == "reject"   # senior vs junior
    assert evaluate(senior_job, _profile(seniority="mid")).bucket == "stretch"     # one step up
    assert evaluate(job, _profile(skills={"python": 1})).bucket == "reject"        # 1 of 6 required


def test_closed_jobs_never_matched(db):
    job = _job(db)
    db.jobs.update_one({"job_id": job["job_id"]}, {"$set": {"status": "closed"}})
    assert match_all(db, _profile()) == []


def test_unverified_flagged(db):
    _job(db, source="linkedin_alerts", verifiable=False)
    [(job, res)] = match_all(db, _profile())
    assert job["status"] == "unverified" and any("не проверено" in n for n in res.notes)


def test_tracker_hides_applied_and_never_downgrades(db):
    job = _job(db)
    set_status(db, job["job_id"], "applied", "sent via company site")
    set_status(db, job["job_id"], "viewed")
    assert db.applications.find_one({})["status"] == "applied"
    assert match_all(db, _profile()) == []
    assert len(match_all(db, _profile(), include_tracked=True)) == 1
    assert list_applications(db)[0]["job_status"] == "open"


def test_interview_builds_profile(db):
    _job(db)
    db.jobs.update_one({}, {"$set": {"extraction": to_document(extract_rules("ML Engineer", AD_DE))}})
    answers = {
        "target_roles": "1", "seniority": "mid", "years_experience": "3",
        "skills_language": "python", "skills_ml_framework": "pytorch", "skills_genai": "",
        "skills_ml_domain": "", "skills_mlops": "docker", "skills_cloud_data": "",
        "market_gaps": "1", "learning": "AWS, rag", "german_level": "B2", "english_level": "C1",
        "remote_pref": "any", "cities": "Berlin, München", "relocate": "nein", "min_salary_year": "0",
        "exclude_companies": "", "experience_story": "Built a RAG chatbot.",
    }

    def ask(q, options):
        if q["qid"].startswith("level_") or q["type"] == "skill_level":
            return "3" if "python" in q["text"] else "2"
        return answers[q["qid"]]

    p = run_interview(db, ask)
    assert p.skills["python"] == 3 and p.skills["pytorch"] == 2 and p.skills["docker"] == 2
    assert len(p.skills) == 4                       # + the first market gap
    assert p.learning == ["aws", "rag"] and p.cities == ["Berlin", "München"]
    assert p.german_level == "B2" and p.min_salary_year is None and p.relocate is False
    assert db.questions.count_documents({}) > 10     # questions live in the DB


def test_profile_from_text_rules():
    p = profile_from_text("5 years Python, PyTorch, Docker. German B1, fluent English.", llm=None)
    assert {"python", "pytorch", "docker"} <= set(p.skills) and p.years_experience == 5
    assert p.german_level == "B1" and p.english_level == "C1"


LINKEDIN_HTML = """<html><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc"><img src="logo.png"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Machine Learning Engineer</a>
<p>Acme GmbH · Berlin, Germany (Hybrid)</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/?refId=x">AI Engineer (m/w/d)</a>
<p>Beta AG</p><p>München</p><a href="https://www.linkedin.com/comm/jobs/view/4099999999/">View job</a></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search">See all jobs</a></td></tr>
</table></body></html>"""


def test_linkedin_alert_parser():
    jobs = parse_alert_html(LINKEDIN_HTML, LinkedInAlertsAdapter.JOB_PATTERN, LinkedInAlertsAdapter.CANONICAL)
    assert [j["id"] for j in jobs] == ["4012345678", "4099999999"]
    assert jobs[0] == {"id": "4012345678", "title": "Machine Learning Engineer",
                       "url": "https://www.linkedin.com/jobs/view/4012345678/",
                       "company": "Acme GmbH", "location": "Berlin, Germany (Hybrid)"}
    assert jobs[1]["company"] == "Beta AG" and jobs[1]["location"] == "München"


def test_manual_page_jsonld():
    page = {"url": "https://careers.example.org/jobs/42", "title": "Jobs", "text": "fallback",
            "jsonld": [json.dumps({"@context": "https://schema.org", "@graph": [{"@type": "JobPosting",
                       "title": "NLP Engineer", "hiringOrganization": {"name": "Example SE"},
                       "jobLocation": {"address": {"addressLocality": "Köln", "addressCountry": "DE"}},
                       "validThrough": "2026-12-31", "description": "<p>spaCy and transformers</p>",
                       "baseSalary": {"currency": "EUR", "value": {"minValue": 60000, "maxValue": 75000, "unitText": "YEAR"}}}]})]}
    p = posting_from_page(page)
    assert (p.title, p.company, p.city, p.salary_min, p.salary_period) == ("NLP Engineer", "Example SE", "Köln", 60000, "year")
    assert p.extra["valid_through"] == "2026-12-31"


def test_reports_render_and_escape(db):
    _job(db, title="<script>alert(1)</script> ML Engineer")
    db.jobs.update_one({}, {"$set": {"extraction": to_document(extract_rules("ML Engineer", AD_DE, "Berlin")),
                                     "url": "javascript:alert(1)"}})
    _job(db, sid="2", source="linkedin_alerts", verifiable=False)
    stats, sources = market_stats(db), source_table(db)
    assert stats["live_jobs"] == 1                 # alert-only job excluded from public aggregates
    assert {s["source"]: s["unique"] for s in sources} == {"greenhouse": 1, "linkedin_alerts": 1}
    public = render_public(stats, sources)
    assert "<script>alert" not in public and "Источники" in public
    private = render_private(market_stats(db, True), sources, match_all(db, _profile(), include_tracked=True), [])
    assert "<script>alert(1)" not in private and "javascript:alert" not in private
    assert safe_url("javascript:x") == "#"
