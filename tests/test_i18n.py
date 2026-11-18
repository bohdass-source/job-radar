from __future__ import annotations

from job_radar.extract.extractor import to_document
from job_radar.extract.rules import extract_rules
from job_radar.find import engine
from job_radar.find import filters as F
from job_radar.find.discover import run_discovery
from job_radar.i18n import M, set_lang, t
from job_radar.match.matcher import evaluate
from job_radar.match.profile import Profile
from job_radar.models import Posting, utcnow
from job_radar.pipeline.store import ingest
from job_radar.report.aggregates import market_stats, source_table
from job_radar.report.html import render_public

from .conftest import FRESHNESS


def test_every_message_has_both_languages_and_same_placeholders():
    import string

    for key, (en, ru) in M.items():
        assert en and ru, key
        fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}
        assert fields(en) == fields(ru), key


def _job(db):
    text = "Dein Profil\n- Python, Docker\n- Verhandlungssicheres Deutsch (C1)\nWir bieten\n- Hybrides Arbeiten"
    ingest(db, Posting(source="greenhouse", source_id="1", title="ML Engineer", company="Acme", city="Berlin",
                       description=text, scope="x"), now=utcnow(), verifiable=True, freshness_cfg=FRESHNESS)
    db.jobs.update_one({}, {"$set": {"extraction": to_document(extract_rules("ML Engineer", text, "Berlin"))}})
    return db.jobs.find_one({})


def test_english_output(db):
    set_lang("en")
    job = _job(db)
    res = evaluate(job, Profile(skills={"python": 2, "docker": 2}, german_level="A2", cities=["Berlin"]))
    assert res.blockers == ["German: C1 required, you have A2"]
    assert F.check(job, F.SearchFilters(cities=["Hamburg"])) == ["city/radius"]
    assert F.SearchFilters(cities=["Berlin"], radius_km=30, no_agency=True).describe() == "Berlin + 30 km; no agencies"
    html = render_public(market_stats(db), source_table(db))
    assert '<html lang="en">' in html and "Required skills" in html and "Обязательные" not in html
    r = engine.run(db, "keywords", F.SearchFilters())
    assert "Results (1)" in engine.write_html(r).read_text()


def test_russian_output(db):
    set_lang("ru")
    job = _job(db)
    res = evaluate(job, Profile(skills={"python": 2, "docker": 2}, german_level="A2", cities=["Berlin"]))
    assert res.blockers == ["Deutsch: требуется C1, у вас A2"]
    assert t("bucket.stretch") == "ДОРАБОТАТЬ"


def test_discover_in_english(db):
    set_lang("en")
    _job(db)
    asked = []
    answers = iter(["1", "1", "3", "3", "7", "6", "", "3", "", "", "1", ""])

    def ask(q, options, multi):
        asked.append((q, options))
        return next(answers)
    _, _, intro, _ = run_discovery(db, ask)
    assert asked[0][0].startswith("1/10. What appeals") and asked[2][1][2].startswith("confidently")
    assert intro.startswith("Direction: ")
