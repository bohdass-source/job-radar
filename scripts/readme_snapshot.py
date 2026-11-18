"""Write the "Dataset snapshot" block of README.md from the current database.

    python scripts/readme_snapshot.py

Aggregates only, computed with the same code as the public report.
"""

from __future__ import annotations

import re

from job_radar.config import ROOT
from job_radar.db import get_db
from job_radar.models import utcnow
from job_radar.report.aggregates import market_stats, source_table


def pct(rows, key):
    return next((p for k, _, p in rows if k == key), 0.0)


def main() -> None:
    db = get_db()
    stats, sources = market_stats(db), source_table(db)
    german_c1 = pct(stats["german"], "C1") + pct(stats["german"], "C2")
    german_b2 = pct(stats["german"], "B2")
    no_german = pct(stats["german"], "none")
    skills = ", ".join(f"{k} {p:.0f}%" for k, _, p in stats["skills_required"][:10])
    cities = ", ".join(f"{k} {n}" for k, n, _ in stats["cities"][:6])
    src_rows = "\n".join(f"| {s['source']} | {s['jobs']} | {s['unique']} | {s['live']} |" for s in sources)
    block = f"""Collected on {utcnow():%Y-%m-%d}. These are aggregates only; closed and expired jobs are excluded.

- **{stats['live_jobs']} open AI/ML jobs in Germany** ({stats['total_jobs_ever']} unique jobs seen, from {stats['postings']} source records after cross-source merging).
- **German:** {german_c1:.0f}% of ads ask for C1 or higher, {german_b2:.0f}% for B2, and {no_german:.0f}% say German is optional.
- **Most required skills:** {skills}.
- **Workplace:** {pct(stats['remote'], 'hybrid'):.0f}% hybrid, {pct(stats['remote'], 'remote'):.0f}% fully remote, {pct(stats['remote'], 'onsite'):.0f}% on-site, and the rest not stated.
- **Cities with the most jobs:** {cities}.

| Source | Jobs | Only from this source | Open |
|---|---:|---:|---:|
{src_rows}

_Extraction method for this snapshot: rule-based. With an LLM gateway configured, seniority and language
levels are extracted more completely._"""
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<!-- SNAPSHOT:START -->.*?<!-- SNAPSHOT:END -->",
                  lambda _: f"<!-- SNAPSHOT:START -->\n{block}\n<!-- SNAPSHOT:END -->", text, flags=re.S)
    path.write_text(text, encoding="utf-8")
    print(block)


if __name__ == "__main__":
    main()
