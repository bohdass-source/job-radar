"""Self-contained HTML reports.

public  -> reports/market.html  : aggregates only, safe to commit and share.
private -> data/private/report.html : adds your three buckets and your tracker.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
from typing import Any

from ..models import utcnow
from .aggregates import _pct
from ..i18n import get_lang, t

ATTRIBUTION = {
    "adzuna": '<a href="https://www.adzuna.co.uk">The Adzuna API</a>',
    "arbeitnow": '<a href="https://www.arbeitnow.com">Arbeitnow</a>',
    "jooble": '<a href="https://jooble.org">Jooble</a>',
    "ba_jobsuche": 'BUNDESAGENTUR FÜR ARBEIT (BA), Jobbörse, <a href="https://www.arbeitsagentur.de/jobsuche/">arbeitsagentur.de/jobsuche</a>',
}

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--card:#fff;--line:#e6e3dc;--bar:#3b6ea5;--bar2:#c7d7ea;
--ok:#2f7d4f;--warn:#a86b00;--bad:#a33a3a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161615;--fg:#ecebe6;--muted:#a3a19a;
--card:#1f1f1d;--line:#34332f;--bar:#7aa7d9;--bar2:#2b3b4d;--ok:#6cc08f;--warn:#e0a640;--bad:#e07a7a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:19px;margin:36px 0 12px}h3{font-size:15px;margin:0 0 10px}.muted{color:var(--muted)}
nav{display:flex;gap:14px;flex-wrap:wrap;margin:16px 0;font-size:14px}nav a{color:var(--bar)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi b{display:block;font-size:24px}.row{display:grid;grid-template-columns:minmax(90px,36%) 1fr 92px;gap:8px;
align-items:center;font-size:13.5px;margin:3px 0}.track{background:var(--bar2);border-radius:4px;height:10px}
.fill{background:var(--bar);border-radius:4px;height:10px}.num{text-align:right;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid
var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}.tablewrap{overflow-x:auto}
.tag{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 8px;margin:1px;font-size:12px}
.miss{border-color:var(--warn);color:var(--warn)}.apply{color:var(--ok)}.stretch{color:var(--warn)}.reject{color:var(--bad)}
a{color:var(--bar)}footer{margin-top:40px;font-size:13px;color:var(--muted)}
"""


def _bars(rows: list[tuple[str, int, float]], label: str = "%") -> str:
    if not rows:
        return f'<p class="muted">{t("h.no_data")}</p>'
    peak = max(r[2] for r in rows) or 1
    out = []
    for name, count, pct in rows:
        out.append(f'<div class="row"><span title="{escape(str(name))}">{escape(str(name))}</span>'
                   f'<div class="track"><div class="fill" style="width:{100 * pct / peak:.1f}%"></div></div>'
                   f'<span class="num">{pct:.0f}{label} <span class="muted">({count})</span></span></div>')
    return "\n".join(out)


def safe_url(url: str | None) -> str:
    """Only http(s) links reach the page; a feed or a sent page cannot inject javascript: URLs."""
    url = (url or "").strip()
    return escape(url) if url.lower().startswith(("https://", "http://")) else "#"


def _card(title: str, body: str) -> str:
    return f'<div class="card"><h3>{escape(title)}</h3>{body}</div>'


def _fmt_dt(v: Any) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if isinstance(v, datetime) else "—"


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="{get_lang()}"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{CSS}</style></head>'
            f"<body><main>{body}</main></body></html>")


def market_section(stats: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    sal = stats["salary"]
    sal_text = (f"{sal['median_min']:,} – {sal['median_max']:,} €".replace(",", " ")
                if sal["median_min"] and sal["median_max"] else "—")
    kpis = "".join(f'<div class="card kpi"><span class="muted">{escape(k)}</span><b>{escape(str(v))}</b></div>' for k, v in [
        (t("h.kpi_live"), stats["live_jobs"]),
        (t("h.kpi_total"), stats["total_jobs_ever"]),
        (t("h.kpi_records"), stats["postings"]),
        (t("h.kpi_salary"), sal_text),
    ])
    src_rows = "".join(
        f"<tr><td>{escape(s['source'])}</td><td class=num>{s['jobs']}</td><td class=num>{s['unique']}</td>"
        f"<td class=num>{s['live']}</td><td>{_fmt_dt(s['last_run'])}</td></tr>" for s in sources)
    status = ", ".join(f"{k}: {v}" for k, v in sorted(stats["status"].items()))
    return f"""
<h2 id="market">{t("h.market")}</h2>
<div class="kpis">{kpis}</div>
<p class="muted">{escape(t("h.market_note", status=status, snip=stats['snippet_share'], n=sal['n']))}</p>
<div class="grid">
{_card(t("h.req_skills"), _bars(stats["skills_required"]))}
{_card(t("h.nice_skills"), _bars(stats["skills_nice"]))}
{_card(t("h.categories"), _bars(stats["skill_categories"]))}
{_card(t("h.german"), _bars(stats["german"]))}
{_card(t("h.english"), _bars(stats["english"]))}
{_card(t("h.workplace"), _bars(stats["remote"]))}
{_card(t("h.seniority"), _bars(stats["seniority"]))}
{_card(t("h.cities"), _bars(stats["cities"]))}
{_card(t("h.ad_lang"), _bars(stats["ad_language"]))}
</div>
<h2 id="sources">{t("h.sources")}</h2>
<p class="muted">{escape(t("h.sources_note"))}</p>
<div class="card tablewrap"><table><tr><th>{t("h.col_source")}</th><th class=num>{t("h.col_jobs")}</th>
<th class=num>{t("h.col_unique")}</th><th class=num>{t("h.col_open")}</th><th>{t("h.col_last_run")}</th></tr>{src_rows}</table></div>"""


def _attribution(sources: list[dict[str, Any]]) -> str:
    names = [ATTRIBUTION[s["source"]] for s in sources if s["source"] in ATTRIBUTION]
    return (t("h.data_sources") + ", ".join(names) + ". ") if names else ""


def render_public(stats: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    body = (f"<h1>{t('h.public_title')}</h1>"
            f'<p class="muted">{t("h.public_sub", ts=f"{utcnow():%Y-%m-%d %H:%M}")}</p>'
            f"<nav><a href='#market'>{t('h.market')}</a><a href='#sources'>{t('h.sources')}</a></nav>"
            + market_section(stats, sources)
            + f"<footer>{_attribution(sources)}{t('h.public_footer')}</footer>")
    return _page("job-radar market", body)


def _job_rows(items: list[tuple[dict[str, Any], Any]], tracked: dict[str, str], show_reasons: bool) -> str:
    if not items:
        return f'<p class="muted">{t("h.none_now")}</p>'
    rows = []
    for job, res in items:
        missing = "".join(f'<span class="tag miss">{escape(s)}</span>' for s in res.missing_required)
        matched = "".join(f'<span class="tag">{escape(s)}</span>' for s in res.matched[:8])
        reasons = "<br>".join(escape(x) for x in (res.blockers if show_reasons and res.bucket == "reject" else res.gaps))
        notes = "<br>".join(escape(x) for x in res.notes)
        verified = _fmt_dt(job.get("last_verified")) if job.get("status") == "open" else t("s.unverified")
        rows.append(
            f"<tr><td><a href='{safe_url(job.get('url'))}' rel='noreferrer'>{escape(job.get('title', ''))}</a><br>"
            f"<span class=muted>{escape(job.get('company', ''))} · {escape(job.get('city') or job.get('location') or '')}"
            f" · <code>{job['job_id']}</code></span></td>"
            f"<td class=num>{res.score:.2f}</td><td>{matched}{missing}</td><td>{reasons}"
            f"{'<br>' if reasons and notes else ''}<span class=muted>{notes}</span></td>"
            f"<td>{escape(tracked.get(job['job_id'], '—'))}</td><td class=muted>{verified}</td></tr>")
    head = (f"<tr><th>{t('col.job')}</th><th class=num>{t('h.score')}</th><th>{t('col.skills_missing')}</th>"
            f"<th>{t('col.reasons')}</th><th>{t('col.status')}</th><th>{t('col.verified')}</th></tr>")
    return f'<div class="card tablewrap"><table>{head}{"".join(rows)}</table></div>'


def render_private(stats: dict[str, Any], sources: list[dict[str, Any]], matches: list[tuple[dict, Any]],
                   applications: list[dict[str, Any]], limit_per_bucket: int = 150) -> str:
    buckets = {b: [m for m in matches if m[1].bucket == b] for b in ("apply", "stretch", "reject")}
    tracked = {a["job_id"]: a["status"] for a in applications}
    learn = Counter(s for _, r in buckets["stretch"] for s in r.missing_required)
    reject_reasons = Counter(r.blockers[0].split(":")[0].split(",")[0] for _, r in buckets["reject"] if r.blockers)
    app_rows = "".join(
        f"<tr><td><a href='{safe_url(a.get('url'))}' rel='noreferrer'>{escape(a.get('title') or a['job_id'])}</a><br>"
        f"<span class=muted>{escape(a.get('company') or '')}</span></td><td>{escape(a['status'])}</td>"
        f"<td>{escape(a.get('job_status', ''))}</td><td>{_fmt_dt(a.get('updated_at'))}</td>"
        f"<td>{escape((a.get('history') or [{}])[-1].get('note', ''))}</td></tr>" for a in applications)
    body = f"""<h1>{t("h.private_title")}</h1>
<p class="muted">{escape(t("h.private_sub", ts=f"{utcnow():%Y-%m-%d %H:%M}"))}</p>
<nav><a href="#apply">{t("h.nav_apply", n=len(buckets['apply']))}</a><a href="#stretch">{t("h.nav_stretch", n=len(buckets['stretch']))}</a>
<a href="#reject">{t("h.nav_reject", n=len(buckets['reject']))}</a><a href="#tracker">{t("h.nav_tracker", n=len(applications))}</a>
<a href="#market">{t("h.market")}</a><a href="#sources">{t("h.sources")}</a></nav>
<h2 id="apply" class="apply">{t("h.apply_title")}</h2>
{_job_rows(buckets['apply'][:limit_per_bucket], tracked, False)}
<h2 id="stretch" class="stretch">{t("h.stretch_title")}</h2>
<div class="grid">{_card(t("h.most_missing"), _bars(_pct(learn, len(buckets['stretch']) or 1, 15)))}</div>
{_job_rows(buckets['stretch'][:limit_per_bucket], tracked, False)}
<h2 id="reject" class="reject">{t("h.reject_title")}</h2>
<div class="grid">{_card(t("h.main_reasons"), _bars(_pct(reject_reasons, len(buckets['reject']) or 1, 10)))}</div>
<details><summary>{t("h.show_rejected", n=len(buckets['reject']))}</summary>
{_job_rows(buckets['reject'][:limit_per_bucket], tracked, True)}</details>
<h2 id="tracker">{t("h.tracker")}</h2>
<div class="card tablewrap"><table><tr><th>{t("col.job")}</th><th>{t("h.col_my_status")}</th><th>{t("h.col_job_now")}</th>
<th>{t("h.col_updated")}</th><th>{t("h.col_note")}</th></tr>{app_rows}</table></div>
{market_section(stats, sources)}
<footer>{_attribution(sources)}{t("h.private_footer")}</footer>"""
    return _page("job-radar personal", body)
