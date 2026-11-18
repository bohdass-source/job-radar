"""One search engine behind the four modes: filter -> (semantic query) -> (profile buckets) -> sort -> render."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from pymongo.database import Database

from ..config import private_path
from ..match.matcher import MatchResult, application_note, evaluate
from ..match.profile import Profile
from ..models import utcnow
from ..report.html import _page, safe_url
from . import filters as F
from ..i18n import t

LIVE = ["open", "unverified"]
def bucket_label(bucket: str | None) -> str:
    return t(f"bucket.{bucket}") if bucket else ""


@dataclass
class Item:
    job: dict[str, Any]
    fit: float
    match: MatchResult | None = None
    semantic: float | None = None

    @property
    def bucket(self) -> str | None:
        return self.match.bucket if self.match else None


@dataclass
class Results:
    mode: str
    filters: F.SearchFilters
    items: list[Item]
    total_live: int
    dropped: Counter[str]
    rejected: list[Item] = field(default_factory=list)
    intro: str = ""


def live_jobs(db: Database, include_tracked: bool = False) -> list[dict[str, Any]]:
    jobs = list(db.jobs.find({"status": {"$in": LIVE}}))
    if include_tracked:
        return jobs
    done = {a["job_id"] for a in db.applications.find({"status": {"$nin": ["viewed", "planned"]}}, {"job_id": 1})}
    return [j for j in jobs if j["job_id"] not in done]


def run(db: Database, mode: str, f: F.SearchFilters, *, profile: Profile | None = None, query: str | None = None,
        embedder: Any = None, index: Any = None, top: int = 50, intro: str = "", lenient_skills: bool = False) -> Results:
    jobs = live_jobs(db)
    total = len(jobs)
    kept, dropped = F.apply(jobs, f)
    by_id = {j["job_id"]: j for j in kept}

    semantic: dict[str, float] = {}
    if query:
        from ..search import DEFAULT_CHUNKER, index_dir
        from ..search.embed import DEFAULT_MODEL, get_embedder
        from ..search.index import VectorIndex

        embedder = embedder or get_embedder(DEFAULT_MODEL)
        index = index or VectorIndex.load(index_dir(DEFAULT_CHUNKER, DEFAULT_MODEL))
        hits = index.search(embedder.encode([query], "query")[0], k=max(top * 3, 100), allowed_jobs=set(by_id))
        semantic = {h.job_id: h.score for h in hits}
        kept = [by_id[j] for j in semantic]

    items: list[Item] = []
    rejected: list[Item] = []
    for job in kept:
        res = evaluate(job, profile, semantic.get(job["job_id"]), lenient_skills=lenient_skills) if profile else None
        if res is not None and semantic:
            fit = 0.5 * res.score + 0.5 * semantic[job["job_id"]]
        elif res is not None:
            fit = res.score
        else:
            fit = semantic.get(job["job_id"], 0.0)
        item = Item(job, round(fit, 3), res, semantic.get(job["job_id"]))
        (rejected if res is not None and res.bucket == "reject" else items).append(item)

    rank = {"apply": 0, "stretch": 1, None: 0}
    ordered = F.sort_jobs([(i.job, i.fit) for i in items], f)
    pos = {j["job_id"]: n for n, (j, _) in enumerate(ordered)}
    items.sort(key=lambda i: (rank.get(i.bucket, 2) if f.sort == "fit" and profile else 0, pos[i.job["job_id"]]))
    return Results(mode, f, items[:top], total, dropped, rejected, intro)


def _job_line(i: Item, f: F.SearchFilters) -> str:
    j = i.job
    d = F.distance_km(j, f)
    ext = j.get("extraction") or {}
    lo, hi = F.yearly_salary(j)
    bits = [F.city_label(j) or "?", F.workplace_of(j)]
    if d is not None and f.cities:
        bits.append(t("f.km", km=f"{d:.0f}"))
    bits.append(f"DE {ext.get('german_level', '?')}")
    if lo or hi:
        bits.append(f"{int(lo or 0):,}–{int(hi or 0):,} €".replace(",", " "))
    if j.get("status") == "unverified":
        bits.append(t("s.unverified"))
    return " · ".join(bits)


def print_results(r: Results, limit: int = 25) -> None:
    print(f"\n{r.intro}" if r.intro else "")
    print(t("s.filters", f=r.filters.describe(), s=r.filters.sort))
    print(t("s.counts", live=r.total_live, kept=r.total_live - sum(r.dropped.values()))
          + (t("s.dropped", d=", ".join(f"{k} {v}" for k, v in r.dropped.most_common(5))) if r.dropped else ""))
    if r.rejected:
        print(t("s.rejected", n=len(r.rejected)))
    for i in r.items[:limit]:
        label = bucket_label(i.bucket)
        print(f"\n{'[' + label + '] ' if label else ''}{i.fit:.2f}  {i.job['job_id']}  {i.job['title']} — {i.job.get('company', '')}")
        print(f"   {_job_line(i, r.filters)}")
        if i.job.get("url"):
            print(f"   {i.job['url']}")
        if i.match:
            if i.match.missing_required:
                print("   " + t("s.missing", v=", ".join(i.match.missing_required)))
            for reason in i.match.gaps + i.match.notes:
                print(f"   - {reason}")


def write_html(r: Results, path: Path | None = None) -> Path:
    path = path or private_path(f"find-{r.mode}.html")

    def rows(items: list[Item]) -> str:
        if not items:
            return f'<p class="muted">{t("s.no_jobs")}</p>'
        out = []
        for i in items:
            j, m = i.job, i.match
            miss = "".join(f'<span class="tag miss">{escape(s)}</span>' for s in (m.missing_required if m else []))
            have = "".join(f'<span class="tag">{escape(s)}</span>' for s in (m.matched[:8] if m else []))
            why = "<br>".join(escape(x) for x in ((m.blockers if m and m.bucket == "reject" else (m.gaps + m.notes)) if m else []))
            note = escape(application_note(m)) if m and m.bucket == "stretch" else ""
            out.append(f"<tr><td><b class='{i.bucket or ''}'>{bucket_label(i.bucket)}</b><br>"
                       f"<a href='{safe_url(j.get('url'))}' rel='noreferrer'>{escape(j.get('title', ''))}</a><br>"
                       f"<span class=muted>{escape(j.get('company', ''))} · {escape(_job_line(i, r.filters))} · "
                       f"<code>{j['job_id']}</code></span></td><td class=num>{i.fit:.2f}</td>"
                       f"<td>{have}{miss}</td><td>{why}{'<br><i>' + note + '</i>' if note else ''}</td></tr>")
        return (f"<div class='card tablewrap'><table><tr><th>{t('col.job')}</th><th class=num>Fit</th>"
                f"<th>{t('col.skills')}</th><th>{t('col.reasons')}</th></tr>{''.join(out)}</table></div>")

    dropped = ", ".join(f"{escape(k)}: {v}" for k, v in r.dropped.most_common()) or "—"
    intro = "".join(f"<p>{escape(p)}</p>" for p in r.intro.split("\n") if p.strip())
    body = (f"<h1>{escape(t('s.html_title', mode=r.mode))}</h1><p class=muted>{utcnow():%Y-%m-%d %H:%M} UTC. "
            f"{t('s.closed_excluded')}</p>{intro}"
            f"<div class=card><b>{t('s.html_filters')}</b> {escape(r.filters.describe())}. "
            f"<b>{t('s.html_sort')}</b> {r.filters.sort}.<br>{t('s.html_counts', live=r.total_live, d=dropped)}</div>"
            f"<h2>{t('s.results', n=len(r.items))}</h2>{rows(r.items)}"
            + (f"<h2>{t('s.no_fit', n=len(r.rejected))}</h2><details><summary>{t('s.show')}</summary>"
               f"{rows(r.rejected[:200])}</details>" if r.rejected else ""))
    path.write_text(_page(f"job-radar find {r.mode}", body), encoding="utf-8")
    return path
