"""job-radar command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta

import yaml

from .config import CONFIG_DIR, REPORTS_DIR, private_path
from .i18n import t


def _db():
    from .db import get_db

    return get_db()


# ---------- sources / companies ----------

def cmd_sources(args: argparse.Namespace) -> None:
    from .sources.base import load_terms
    from .sources.registry import ADAPTERS, source_config

    full = source_config()
    cfg = full.get("sources") or {}
    overrides = full.get("terms_overrides") or {}
    print(f"{'source':17} {'enabled':8} {'terms':13} {'checked':11} {'verifies':8} rate limit")
    for name in ADAPTERS:
        card = load_terms(name)
        if name in overrides:
            card.status = f"{overrides[name]}*"
        on = "yes" if (cfg.get(name) or {}).get("enabled") else "no"
        stale = " (re-check!)" if card.stale else ""
        print(f"{name:17} {on:8} {card.status:13} {card.checked!s:11} {'yes' if card.verifiable else 'no':8} {card.rate_limit}{stale}")
        if args.verbose:
            for u in card.terms_urls:
                print(f"{'':17} terms: {u}")
    print("\nA source runs only when enabled in config/sources.yaml AND its card docs/sources/<name>.md says status: approved.")
    if overrides:
        print("* status overridden locally in config/sources.local.yaml")


def cmd_companies(args: argparse.Namespace) -> None:
    from .sources.ats import detect_ats

    path = CONFIG_DIR / "companies.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    companies = data.get("companies") or []
    if args.action == "list":
        for c in companies:
            print(f"{c.get('name', ''):30} {c['ats']:15} {c['slug']}{' (eu)' if c.get('eu') else ''}"
                  f"{'' if c.get('enabled', True) else ' [disabled]'}")
        return
    found = detect_ats(args.url)
    if not found:
        sys.exit("Could not recognise the ATS from this URL (supported: Greenhouse, Lever, Recruitee, "
                 "SmartRecruiters, Ashby, Personio).")
    ats, slug, eu = found
    if any(c["ats"] == ats and c["slug"] == slug for c in companies):
        print(f"already present: {ats}/{slug}")
        return
    entry = {"name": args.name or slug, "ats": ats, "slug": slug, "enabled": True}
    if eu and ats == "lever":
        entry["eu"] = True
    companies.append(entry)
    data["companies"] = companies
    header = "".join(l + "\n" for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("#"))
    path.write_text(header + "\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"added {entry}")


# ---------- pipeline ----------

def cmd_collect(args: argparse.Namespace) -> None:
    from .pipeline.collect import collect

    for s in collect(_db(), args.source or None, dry_run=args.dry_run):
        done = ", ".join(s.completed_scopes) or "none"
        print(f"{s.source}: fetched {s.fetched}, relevant {s.relevant}, new jobs {s.new_jobs}, "
              f"merged into existing {s.merged_into_existing}, requests {s.requests}, complete listings: {done}")
        for e in s.errors:
            print(f"  ! {e}")


def cmd_refresh(args: argparse.Namespace) -> None:
    from .pipeline.collect import refresh

    print(json.dumps(refresh(_db(), limit=args.limit), indent=1))


def cmd_prune(args: argparse.Namespace) -> None:
    from .pipeline.collect import prune

    print(prune(_db(), dry_run=args.dry_run))


def cmd_extract(args: argparse.Namespace) -> None:
    from .extract.extractor import extract_all

    print(extract_all(_db(), method=args.method, limit=args.limit, force=args.force))


def cmd_index(args: argparse.Namespace) -> None:
    from .search import build_index, index_dir

    idx = build_index(_db(), args.chunker, args.model)
    print(f"indexed {idx.meta['jobs']} jobs as {idx.meta['chunks']} chunks -> {index_dir(args.chunker, args.model)}")


def cmd_search(args: argparse.Namespace) -> None:
    from .search import search

    db = _db()
    hits = search(db, args.query, k=args.k, chunker=args.chunker, model=args.model)
    jobs = {j["job_id"]: j for j in db.jobs.find({"job_id": {"$in": [h.job_id for h in hits]}})}
    for h in hits:
        j = jobs.get(h.job_id, {})
        flag = "" if j.get("status") == "open" else f" [{j.get('status')}]"
        print(f"{h.score:.3f}  {h.job_id}  {j.get('title')} — {j.get('company')}, {j.get('city') or ''}{flag}")
        print(f"        {h.text[:160]}...")


def cmd_show(args: argparse.Namespace) -> None:
    from .tracker import set_status

    db = _db()
    job = db.jobs.find_one({"job_id": args.job_id})
    if not job:
        sys.exit("no such job")
    set_status(db, args.job_id, "viewed")
    print(f"{job['title']} — {job.get('company')} ({job.get('city') or job.get('location')})")
    print(f"status: {job['status']} ({job.get('status_reason')}); last verified: {job.get('last_verified')}")
    print(f"link: {job.get('url')}")
    print("sources: " + ", ".join(f"{r['source']}{' (gone)' if r.get('gone') else ''}" for r in job["sources"]))
    if job.get("extraction"):
        print(json.dumps(job["extraction"], ensure_ascii=False, indent=1))
    print("\n" + job.get("description", "")[:3000])


# ---------- profile / match / tracker ----------

def cmd_profile(args: argparse.Namespace) -> None:
    from pathlib import Path

    from .extract.llm import LLMClient
    from .match.interview import cv_draft_markdown, run_interview, seed_questions, terminal_ask
    from .match.profile import load_profile, profile_from_text, read_document, save_profile

    db = _db()
    if args.action == "import":
        profile = profile_from_text(read_document(Path(args.file)), LLMClient())
        save_profile(db, profile)
        print(f"profile saved ({len(profile.skills)} skills). Run `job-radar profile interview --fill-gaps` "
              "to confirm levels and add what the CV does not say.")
    elif args.action == "interview":
        base = load_profile(db) if args.fill_gaps else None
        profile = run_interview(db, terminal_ask, base=base, only_missing=args.fill_gaps)
        save_profile(db, profile)
        path = private_path("cv_draft.md")
        path.write_text(cv_draft_markdown(profile), encoding="utf-8")
        print(f"\nprofile saved; CV draft: {path}")
    elif args.action == "seed-questions":
        print(f"seeded {seed_questions(db, overwrite=True)} questions into MongoDB")
    elif args.action == "cv-draft":
        profile = load_profile(db) or sys.exit("no profile yet")
        path = private_path("cv_draft.md")
        path.write_text(cv_draft_markdown(profile), encoding="utf-8")
        print(path)
    else:
        profile = load_profile(db)
        print(profile.model_dump_json(indent=1) if profile else "no profile yet")


def _profile_or_exit(db):
    from .match.profile import load_profile

    profile = load_profile(db)
    if not profile:
        sys.exit("No profile yet: run `job-radar profile import <cv.pdf>` or `job-radar profile interview`.")
    return profile


def cmd_match(args: argparse.Namespace) -> None:
    from .match.matcher import application_note, match_all
    from .search.embed import get_embedder

    db = _db()
    profile = _profile_or_exit(db)
    emb = get_embedder(args.model) if args.semantic else None
    results = match_all(db, profile, embedder=emb, include_tracked=args.all)
    labels = {b: t(f"bucket.{b}") for b in ("apply", "stretch", "reject")}
    counts = {b: sum(r.bucket == b for _, r in results) for b in labels}
    print("  ".join(f"{labels[b]}: {n}" for b, n in counts.items()))
    for job, res in results:
        if args.bucket != "all" and res.bucket != args.bucket:
            continue
        if args.bucket == "all" and res.bucket == "reject":
            continue
        flag = f" [{t('s.unverified')}]" if job.get("status") == "unverified" else ""
        print(f"\n[{labels[res.bucket]}] {res.score:.2f}  {job['job_id']}  {job['title']} — {job.get('company')}, "
              f"{job.get('city') or job.get('location') or ''}{flag}")
        if job.get("url"):
            print(f"   {job['url']}")
        if res.missing_required:
            print("   " + t("s.missing", v=", ".join(res.missing_required)))
        for reason in res.blockers + res.gaps + res.notes:
            print(f"   - {reason}")
        if res.bucket == "stretch" and application_note(res):
            print("   " + t("s.cover_note", v=application_note(res)))
        args.limit -= 1
        if args.limit <= 0:
            break


def cmd_track(args: argparse.Namespace) -> None:
    from .tracker import list_applications, set_status

    db = _db()
    if args.job_id == "list":
        for a in list_applications(db, args.status):
            closed = t("c.job_closed") if a["job_status"] in ("closed", "expired") else ""
            print(f"{a['status']:10} {a['job_id']}  {a.get('title')} — {a.get('company')}{closed}")
        return
    if not args.status:
        sys.exit("give a status: viewed | planned | applied | interview | offer | rejected | withdrawn")
    a = set_status(db, args.job_id, args.status, args.note or "")
    print(f"{a['job_id']}: {a['status']}")


def cmd_ask(args: argparse.Namespace) -> None:
    from .rag import ask

    ans = ask(_db(), args.question, k=args.k)
    print(ans.text)
    if ans.used_llm:
        print("\n" + t("c.sources") + ", ".join(sorted({h.job_id for h in ans.hits})))


def cmd_report(args: argparse.Namespace) -> None:
    from .report.aggregates import market_stats, source_table
    from .report.html import render_private, render_public
    from .tracker import list_applications

    db = _db()
    stats, sources = market_stats(db), source_table(db)
    REPORTS_DIR.mkdir(exist_ok=True)
    public = REPORTS_DIR / "market.html"
    public.write_text(render_public(stats, sources), encoding="utf-8")
    print(f"public report (aggregates only): {public}")
    if args.public_only:
        return
    from .match.matcher import match_all
    from .match.profile import load_profile

    profile = load_profile(db)
    matches = match_all(db, profile, include_tracked=True) if profile else []
    private = private_path("report.html")
    private.write_text(render_private(market_stats(db, include_private_sources=True), sources, matches,
                                      list_applications(db)), encoding="utf-8")
    print(f"personal report: {private}")


def cmd_digest(args: argparse.Namespace) -> None:
    from .digest import send_digest

    db = _db()
    print(send_digest(db, _profile_or_exit(db), dry_run=args.dry_run))


def cmd_inbox(args: argparse.Namespace) -> None:
    from .inbox import bookmarklet, serve

    if args.bookmarklet:
        print(bookmarklet())
    else:
        serve()


def cmd_stats(args: argparse.Namespace) -> None:
    db = _db()
    by_status = {d["_id"]: d["n"] for d in db.jobs.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
    print(f"unique jobs: {db.jobs.count_documents({})}  postings: {db.postings.count_documents({})}  "
          f"by status: {by_status}  extracted: {db.jobs.count_documents({'extraction': {'$ne': None}})}")


# ---------- find: four search modes ----------

def _filters_from_args(args: argparse.Namespace, base=None):
    from .find.filters import SearchFilters

    f = base or SearchFilters()
    upd = {}
    if args.city:
        upd["cities"] = args.city
    if args.radius is not None:
        upd["radius_km"] = args.radius
    if args.workplace:
        upd["workplace"] = args.workplace
    for key in ("salary_min", "salary_max", "german", "english"):
        val = getattr(args, key)
        if val is not None:
            upd[{"german": "german_max", "english": "english_max"}.get(key, key)] = val
    if args.seniority:
        upd["seniority"] = args.seniority
    for flag, key in (("only_with_salary", "only_with_salary"), ("no_agency", "no_agency"), ("permanent", "permanent_only"),
                      ("quereinstieg", "career_changer_ok"), ("no_internships", "exclude_internships")):
        if getattr(args, flag):
            upd[key] = True
    if args.fulltime:
        upd["fulltime"] = True
    if args.parttime:
        upd["fulltime"] = False
    if args.days:
        upd["posted_within_days"] = args.days
    if args.exclude_company:
        upd["exclude_companies"] = f.exclude_companies + args.exclude_company
    if args.sort:
        upd["sort"] = args.sort
    return f.model_copy(update=upd)


def _numbered_ask(question: str, options: list[str], multi: bool) -> str:
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    hint = t("c.pick_many") if multi else (t("c.pick_one") if options else "")
    return input(f">{hint} ").strip()


def cmd_find(args: argparse.Namespace) -> None:
    from pathlib import Path

    from .extract.llm import LLMClient
    from .find import engine
    from .match.profile import load_profile, save_profile

    db = _db()
    profile, base, query, intro = None, None, None, ""
    if args.mode == "portfolio":
        from .find.portfolio import profile_from_portfolio

        if not args.inputs:
            sys.exit("give folders, files or github:<user>")
        profile, projects, evidence = profile_from_portfolio(args.inputs, LLMClient())
        intro = t("c.portfolio", n=len(projects), v=", ".join(f"{s} ({n})" for s, n in evidence.most_common(15)))
    elif args.mode == "questionnaire":
        from .find.questionnaire import process, template

        if args.template or not args.inputs:
            path = template(db, private_path("questionnaire.md"))
            print(t("c.q_written", path=path))
            return
        profile, base, result, method = process(Path(args.inputs[0]).read_text(encoding="utf-8"), LLMClient())
        intro = (t("c.q_done", how=t("c.q_how_llm") if method == "llm" else t("c.q_how_rules"), n=len(profile.skills),
                   de=profile.german_level, en=profile.english_level)
                 + (t("c.q_other", v="; ".join(result.other_restrictions)) if result.other_restrictions else ""))
    elif args.mode == "keywords":
        from .find.filters import SearchFilters
        from .find.keywords import parse_query

        if not args.inputs:
            sys.exit('give keywords, e.g.: job-radar find keywords "LLM RAG Berlin +python -praktikum"')
        query, base = parse_query(" ".join(args.inputs), _filters_from_args(args, SearchFilters()))
        profile = load_profile(db) if args.use_profile else None
        intro = t("c.query", q=query)
    elif args.mode == "discover":
        from .find.discover import run_discovery

        profile, base, intro, _ = run_discovery(db, _numbered_ask)
    if profile is not None:
        # --german / --english describe YOU: they fill the profile as well as the filter.
        langs = {k: v for k, v in (("german_level", args.german), ("english_level", args.english)) if v}
        if langs:
            profile = profile.model_copy(update=langs)
        if args.city and not profile.cities:
            profile = profile.model_copy(update={"cities": args.city})
        if profile.german_level == "unknown":
            print(t("c.hint_german"))
    filters = _filters_from_args(args, base)
    results = engine.run(db, args.mode, filters, profile=profile, query=query, top=args.top, intro=intro,
                         lenient_skills=args.mode == "discover")
    engine.print_results(results, limit=args.top)
    path = engine.write_html(results)
    print(f"\nHTML: {path}")
    if profile is not None and args.save_profile:
        save_profile(db, profile)
        print(t("c.profile_saved"))


# ---------- bench ----------

def cmd_bench(args: argparse.Namespace) -> None:
    from .bench import write_readme_block

    db = _db()
    if args.action == "label-retrieval":
        from .bench import retrieval

        if args.judge == "llm":
            from .extract.llm import LLMClient
            from pydantic import BaseModel

            class Verdict(BaseModel):
                relevant: bool

            llm = LLMClient()

            def judge(q: str, job: dict) -> bool:
                return llm.complete_json(
                    "Decide if the job ad is a relevant result for the job-search query. Answer JSON {\"relevant\": bool}.",
                    f"Query: {q}\n\nJob: {job.get('title')} @ {job.get('company')}\n{job.get('description', '')[:2500]}",
                    Verdict).relevant
        else:
            def judge(q: str, job: dict) -> bool:
                print(f"\nQUERY: {q}\nJOB:   {job.get('title')} — {job.get('company')}\n       "
                      f"{job.get('description', '')[:400]}")
                return input("relevant? [y/N] ").strip().lower() in ("y", "yes", "д", "да")
        print(f"qrels saved to {retrieval.label(db, judge, args.judge)}")
    elif args.action == "export-pool":
        from .bench import retrieval

        print(f"pool written to {retrieval.export_pool(db)}")
    elif args.action == "import-qrels":
        from .bench import retrieval
        from pathlib import Path

        print(f"qrels written to {retrieval.import_judgments(Path(args.file), args.judge)}")
    elif args.action == "retrieval":
        from .bench import retrieval

        rows = retrieval.run(db)
        qrels = json.loads(private_path(retrieval.QRELS).read_text())
        judges = sorted({v.get("judge", "unknown") for v in qrels.values()})
        judge = ", ".join("an LLM (verify!)" if j == "llm" else j for j in judges)
        md = retrieval.to_markdown(rows, judge, len(retrieval.corpus(db)))
        print(md)
        if args.write_readme:
            write_readme_block("RETRIEVAL", md)
    elif args.action == "label-extraction":
        from .bench import extraction

        path = extraction.label(db, n=args.n)
        print(f"edit {path}: correct each 'gold' block and set \"verified\": true")
    elif args.action == "extraction":
        from .bench import extraction

        results, n_verified = extraction.run(db)
        if n_verified < 50:
            print(f"warning: only {n_verified} verified gold rows (target 50)")
        md = extraction.to_markdown(results)
        print(md)
        if args.write_readme:
            write_readme_block("EXTRACTION", md)


# ---------- scheduler ----------

def cmd_schedule(args: argparse.Namespace) -> None:
    from .digest import send_digest
    from .extract.extractor import extract_all
    from .match.profile import load_profile
    from .pipeline.collect import collect, refresh
    from .search import build_index

    log = logging.getLogger("schedule")
    last_digest = datetime.min
    while True:
        db = _db()
        try:
            collect(db)
            refresh(db)
            extract_all(db)
            build_index(db)
            profile = load_profile(db)
            if profile and datetime.now() - last_digest > timedelta(days=7) and datetime.now().weekday() == args.digest_weekday:
                log.info(send_digest(db, profile))
                last_digest = datetime.now()
        except Exception:
            log.exception("scheduled cycle failed")
        time.sleep(args.every_hours * 3600)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="job-radar", description="AI/ML job market radar for Germany")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--lang", choices=["en", "ru"], help="output language (default: JOB_RADAR_LANG or en)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sources", help="list sources, terms status, rate limits")
    s.set_defaults(fn=cmd_sources)

    s = sub.add_parser("companies", help="list companies or add one from its careers URL")
    s.add_argument("action", choices=["list", "add"])
    s.add_argument("url", nargs="?")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_companies)

    s = sub.add_parser("collect", help="collect from approved, enabled sources")
    s.add_argument("--source", action="append", help="only this source (repeatable)")
    s.add_argument("--dry-run", action="store_true", help="print relevant postings, store nothing")
    s.set_defaults(fn=cmd_collect)

    s = sub.add_parser("refresh", help="re-check open jobs, close dead ones, apply TTLs")
    s.add_argument("--limit", type=int, default=300)
    s.set_defaults(fn=cmd_refresh)

    s = sub.add_parser("prune", help="re-apply relevance/Germany rules to stored jobs, delete the misses")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_prune)

    s = sub.add_parser("extract", help="structured extraction (LLM via gateway, rules fallback)")
    s.add_argument("--method", choices=["auto", "llm", "rules"], default="auto")
    s.add_argument("--limit", type=int)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_extract)

    for name, fn, help_ in [("index", cmd_index, "build the vector index"), ("search", cmd_search, "semantic search")]:
        s = sub.add_parser(name, help=help_)
        if name == "search":
            s.add_argument("query")
            s.add_argument("-k", type=int, default=10)
        s.add_argument("--chunker", default="section", choices=["fixed", "sentence", "section"])
        s.add_argument("--model", default="e5", choices=["e5", "minilm", "hashing"])
        s.set_defaults(fn=fn)

    s = sub.add_parser("show", help="show one job (marks it as viewed)")
    s.add_argument("job_id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("profile", help="import CV, guided interview, show")
    s.add_argument("action", choices=["import", "interview", "show", "seed-questions", "cv-draft"])
    s.add_argument("file", nargs="?")
    s.add_argument("--fill-gaps", action="store_true", help="interview: only ask what the profile lacks")
    s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("match", help="your jobs in three buckets")
    s.add_argument("--bucket", choices=["apply", "stretch", "reject", "all"], default="all")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--all", action="store_true", help="include jobs you already applied to")
    s.add_argument("--semantic", action="store_true", help="add embedding similarity to the score")
    s.add_argument("--model", default="e5")
    s.set_defaults(fn=cmd_match)

    s = sub.add_parser("track", help="track applications: track <job_id> <status> | track list")
    s.add_argument("job_id")
    s.add_argument("status", nargs="?")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_track)

    s = sub.add_parser("ask", help="MiniRAG question over live jobs")
    s.add_argument("question")
    s.add_argument("-k", type=int, default=8)
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("report", help="HTML reports (public aggregates + personal)")
    s.add_argument("--public-only", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("digest", help="weekly digest e-mail")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_digest)

    s = sub.add_parser("inbox", help="run the bookmarklet receiver, or print the bookmarklet")
    s.add_argument("--bookmarklet", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("find", help="search: portfolio | questionnaire | keywords | discover, with filters")
    s.add_argument("mode", choices=["portfolio", "questionnaire", "keywords", "discover"])
    s.add_argument("inputs", nargs="*", help="portfolio: folders/files/github:<user>; questionnaire: filled file; keywords: words")
    s.add_argument("--template", action="store_true", help="questionnaire: write an empty questionnaire")
    s.add_argument("--city", action="append", help="repeatable")
    s.add_argument("--radius", type=int, help="km around --city")
    s.add_argument("--workplace", action="append", choices=["remote", "hybrid", "onsite"], help="repeatable")
    s.add_argument("--salary-min", type=int)
    s.add_argument("--salary-max", type=int)
    s.add_argument("--only-with-salary", action="store_true")
    s.add_argument("--german", choices=["none", "A1", "A2", "B1", "B2", "C1", "C2"], help="your level; jobs needing more are hidden")
    s.add_argument("--english", choices=["none", "A1", "A2", "B1", "B2", "C1", "C2"])
    s.add_argument("--seniority", action="append", choices=["intern", "junior", "mid", "senior", "lead"])
    s.add_argument("--no-agency", action="store_true", help="hide staffing agencies / Zeitarbeit")
    s.add_argument("--fulltime", action="store_true")
    s.add_argument("--parttime", action="store_true")
    s.add_argument("--permanent", action="store_true", help="unbefristet only")
    s.add_argument("--quereinstieg", action="store_true", help="BA: suitable for career changers")
    s.add_argument("--no-internships", action="store_true")
    s.add_argument("--days", type=int, help="published within N days")
    s.add_argument("--exclude-company", action="append")
    s.add_argument("--sort", choices=["fit", "date", "salary", "language", "distance"])
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--use-profile", action="store_true", help="keywords: also sort into buckets with your saved profile")
    s.add_argument("--save-profile", action="store_true", help="store the profile this mode built")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("stats", help="counts")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("bench", help="benchmarks")
    s.add_argument("action", choices=["label-retrieval", "export-pool", "import-qrels", "retrieval",
                                      "label-extraction", "extraction"])
    s.add_argument("file", nargs="?", help="import-qrels: JSON {qid: [relevant job ids]}")
    s.add_argument("--judge", default="human", help="who judged: human | llm | a model name")
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--write-readme", action="store_true")
    s.set_defaults(fn=cmd_bench)

    s = sub.add_parser("schedule", help="loop: collect, refresh, extract, index; weekly digest")
    s.add_argument("--every-hours", type=float, default=24)
    s.add_argument("--digest-weekday", type=int, default=0, help="0 = Monday")
    s.set_defaults(fn=cmd_schedule)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    from .i18n import set_lang

    set_lang(args.lang)
    from .sources.base import SourceNotApproved

    try:
        args.fn(args)
    except SourceNotApproved as exc:
        sys.exit(f"not collecting: {exc}")


if __name__ == "__main__":
    main()
