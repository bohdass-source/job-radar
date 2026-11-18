from __future__ import annotations

import base64

import httpx

from job_radar.extract.extractor import to_document
from job_radar.extract.rules import extract_rules
from job_radar.find import engine
from job_radar.find import filters as F
from job_radar.find.discover import run_discovery
from job_radar.find.keywords import parse_query
from job_radar.find.portfolio import profile_from_portfolio, projects_from_github
from job_radar.find.questionnaire import process, template
from job_radar.http import PoliteClient
from job_radar.models import Posting, utcnow
from job_radar.pipeline.store import ingest
from job_radar.search.embed import HashingEmbedder
from job_radar.search.index import VectorIndex

from .conftest import FRESHNESS

TEXT = """Deine Aufgaben
- LLM-Anwendungen mit RAG und Python bauen
Dein Profil
- Erfahrung mit Python, Docker und SQL
- {german}
Wir bieten
- {workplace}"""


def _add(db, sid, title, city, company="Acme", german="Gute Deutschkenntnisse", workplace="Hybrides Arbeiten",
         extra=None, source="greenhouse"):
    text = TEXT.format(german=german, workplace=workplace)
    ingest(db, Posting(source=source, source_id=sid, title=title, company=company, city=city, description=text,
                       scope="x", extra=extra or {}), now=utcnow(), verifiable=True, freshness_cfg=FRESHNESS)
    job = db.jobs.find_one({"sources.source_id": sid})
    db.jobs.update_one({"job_id": job["job_id"]}, {"$set": {"extraction": to_document(extract_rules(title, text, city))}})
    return db.jobs.find_one({"job_id": job["job_id"]})


def test_radius_workplace_language_agency_filters(db):
    potsdam = _add(db, "1", "LLM Engineer", "Potsdam")                       # ~27 km from Berlin
    munich = _add(db, "2", "LLM Engineer", "München", company="Beta")
    remote = _add(db, "3", "LLM Engineer", "München", company="Gamma", workplace="100% remote in Deutschland")
    c1 = _add(db, "4", "LLM Engineer", "Berlin", company="Delta", german="Verhandlungssicheres Deutsch (C1)")
    agency = _add(db, "5", "LLM Engineer", "Berlin", company="Brunel GmbH")
    f = F.SearchFilters(cities=["Berlin"], radius_km=50, german_max="B2", no_agency=True)
    assert F.check(potsdam, f) == []
    assert F.check(munich, f) == ["город/радиус"]
    assert F.check(remote, f) == []                                          # remote ignores the city
    assert F.check(c1, f) == ["немецкий выше вашего уровня"]
    assert F.check(agency, f) == ["кадровое агентство"]
    assert F.check(munich, F.SearchFilters(workplace=["remote"])) == ["формат работы"]
    assert F.check(potsdam, F.SearchFilters(cities=["Berlin"], radius_km=0)) == ["город/радиус"]


def test_ba_attrs_filters(db):
    job = _add(db, "ba1", "KI Engineer", "Berlin", source="ba_jobsuche",
               extra={"fulltime": False, "parttime": True, "contract": "BEFRISTET", "career_changer_ok": False,
                      "lat": 52.5, "lon": 13.4})
    assert set(F.check(job, F.SearchFilters(fulltime=True, permanent_only=True, career_changer_ok=True))) == \
        {"не полная занятость", "срочный договор", "не для Quereinstieg"}
    assert F.job_coords(job) == (52.5, 13.4)


def test_sort_by_language():
    en = {"job_id": "a", "language": "en", "extraction": {"german_level": "none"}}
    de_c1 = {"job_id": "b", "language": "de", "extraction": {"german_level": "C1"}}
    de_unknown = {"job_id": "c", "language": "de", "extraction": {"german_level": "unknown"}}
    out = F.sort_jobs([(de_c1, 0.9), (de_unknown, 0.8), (en, 0.1)], F.SearchFilters(sort="language"))
    assert [j["job_id"] for j, _ in out] == ["a", "c", "b"]


def test_keyword_query_parsing():
    q, f = parse_query("LLM RAG Berlin +python -praktikum", F.SearchFilters())
    assert q == "LLM RAG python" and f.cities == ["Berlin"]
    assert f.keywords_all == ["python"] and f.keywords_none == ["praktikum"]


def test_keywords_mode_end_to_end(db):
    _add(db, "1", "LLM Engineer", "Berlin")
    _add(db, "2", "Computer Vision Engineer", "Berlin", company="Beta")
    jobs = list(db.jobs.find({}))
    emb = HashingEmbedder()
    idx = VectorIndex.build(jobs, emb, "section")
    q, f = parse_query("LLM RAG Berlin -vision", F.SearchFilters())
    r = engine.run(db, "keywords", f, query=q, embedder=emb, index=idx)
    assert [i.job["title"] for i in r.items] == ["LLM Engineer"]
    assert r.dropped["исключённое слово"] == 1
    assert engine.write_html(r).exists()


def test_portfolio_folder(tmp_path):
    p1 = tmp_path / "rag-bot"
    p1.mkdir()
    (p1 / "README.md").write_text("A RAG chatbot over PDFs using LangChain and FastAPI. Deployed with Docker.")
    (p1 / "requirements.txt").write_text("torch==2.3\ntransformers>=4.40\nqdrant-client\n")
    for name in ("cv-demo", "forecast"):
        d = tmp_path / name
        d.mkdir()
        (d / "README.md").write_text("Python project with Docker. " + ("OpenCV detection" if name == "cv-demo" else "time series forecasting"))
    profile, projects, evidence = profile_from_portfolio([str(tmp_path)], llm=None)
    assert len(projects) == 3
    assert profile.skills["docker"] == 3 and profile.skills["python"] == 3     # in all 3 projects
    assert profile.skills["pytorch"] == 2 and profile.skills["vector databases"] == 2   # from requirements.txt
    assert {"rag", "langchain", "fastapi", "computer vision", "time series"} <= set(profile.skills)
    assert profile.source == "portfolio"


def test_portfolio_github_mocked():
    readme = base64.b64encode(b"LLM agent with tool calling, evaluated with evals. Uses PyTorch.").decode()

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/users/someone/repos":
            return httpx.Response(200, json=[
                {"name": "agent", "description": "agents", "topics": ["llm"], "fork": False,
                 "languages_url": "https://api.github.com/repos/someone/agent/languages"},
                {"name": "forked", "fork": True, "languages_url": "x"}])
        if path.endswith("/languages"):
            return httpx.Response(200, json={"Python": 100, "Dockerfile": 5})
        if path.endswith("/readme"):
            return httpx.Response(200, json={"content": readme})
        return httpx.Response(404)
    http = PoliteClient(min_interval_s=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    [proj] = projects_from_github("someone", http)
    assert proj.name == "agent" and {"python", "docker"} <= proj.skills and "PyTorch" in proj.text


ANSWERS = """# Анкета
## target_roles: Какие роли?
LLM Engineer, ML Engineer
## seniority: уровень
junior
## years_experience: лет
2 года
## skills_language: языки
Python — продакшен, SQL — проекты
## skills_genai: LLM
RAG и LangChain в проектах
## german_level: немецкий
B1
## english_level: английский
C1
## cities: города
Berlin, Leipzig
## radius_km: радиус
40 км
## workplace: формат
гибрид или удалённо
## salary: зарплата
55 000 – 70 000
## restrictions: ограничения
без кадровых агентств, только полная занятость
## experience_story: проекты
Built a RAG assistant for internal documents.
"""


def test_questionnaire_rules_fallback(db, tmp_path):
    path = template(db, tmp_path / "q.md")
    assert "## german_level" in path.read_text() and "## restrictions" in path.read_text()
    profile, f, result, method = process(ANSWERS, llm=None)
    assert method == "rules"
    assert profile.skills["python"] == 3 and profile.skills["sql"] == 2 and {"rag", "langchain"} <= set(profile.skills)
    assert profile.german_level == "B1" and profile.english_level == "C1" and profile.years_experience == 2
    assert f.cities == ["Berlin", "Leipzig"] and f.radius_km == 40
    assert set(f.workplace) == {"remote", "hybrid"} and (f.salary_min, f.salary_max) == (55000, 70000)
    assert f.no_agency and f.fulltime is True and f.german_max == "B1"


def test_questionnaire_llm_path():
    import json

    payload = {"skills": {"PyTorch": 3}, "seniority": "mid", "years_experience": 4, "german_level": "B2",
               "english_level": "C1", "cities": ["Hamburg"], "radius_km": 20, "workplace": ["hybrid"],
               "salary_min": 70000, "no_agency": True, "summary": "ML engineer"}
    from job_radar.extract.llm import LLMClient

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}))
    profile, f, _, method = process("free text", LLMClient(base_url="http://gw", api_key="", model="m", transport=transport))
    assert method == "llm" and profile.skills == {"pytorch": 3} and f.cities == ["Hamburg"] and f.no_agency


def test_discover_sequential(db):
    for i in range(5):
        _add(db, f"l{i}", f"LLM Engineer {i}", "Berlin", company=f"C{i}")
    _add(db, "d1", "Data Scientist", "Berlin", company="D")
    script = iter([
        "2",          # likes: products on LLMs
        "1",          # background: developer
        "3",          # programming: confident
        "2",          # maths: basic
        "5",          # German: 5th option = B2
        "6",          # English C1
        "Berlin",     # cities
        "25",         # radius
        "2",          # hybrid or remote
        "",           # salary
        "1",          # restriction: no agencies
        "1",          # choose first suggested role
        "1, 2",       # skills I have from the top list
    ])
    asked = []

    def ask(q, options, multi):
        asked.append(q)
        return next(script)
    profile, f, intro, facts = run_discovery(db, ask)
    assert asked[0].startswith("1/10") and asked[10].startswith("10/10")   # + the radius follow-up
    assert facts[0].role_id == "llm_engineer" and facts[0].jobs == 5
    assert f.cities == ["Berlin"] and f.radius_km == 25 and f.no_agency and f.german_max == "B2"
    assert f.title_regex and profile.target_roles == ["LLM / GenAI Engineer"] and len(profile.skills) == 2
    r = engine.run(db, "discover", f, profile=profile)
    assert r.items and all("LLM" in i.job["title"] for i in r.items)
    assert "Что выучить" in intro
