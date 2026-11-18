"""Fill a SEPARATE demo database with synthetic job ads, to try every command
without touching any real source. Usage:

    MONGO_DB=job_radar_demo python scripts/seed_demo.py
    MONGO_DB=job_radar_demo job-radar extract --method rules
    MONGO_DB=job_radar_demo job-radar report

All companies and ads below are invented.
"""

from __future__ import annotations

import os
import random

from job_radar.db import get_db
from job_radar.models import Posting, utcnow
from job_radar.pipeline.store import ingest

if os.environ.get("MONGO_DB", "job_radar") == "job_radar":
    raise SystemExit("Refusing to seed the main database. Set MONGO_DB=job_radar_demo.")

CITIES = ["Berlin", "München", "Hamburg", "Köln", "Frankfurt am Main", "Stuttgart", "Leipzig"]
ROLES = [("Machine Learning Engineer", ["Python", "PyTorch", "Docker", "Kubernetes", "MLflow"]),
         ("Senior LLM Engineer", ["Python", "LLM", "RAG", "LangChain", "vector databases", "AWS"]),
         ("Junior Data Scientist", ["Python", "SQL", "scikit-learn", "pandas", "Statistik"]),
         ("MLOps Engineer", ["Kubernetes", "Terraform", "CI/CD", "Docker", "GCP"]),
         ("Computer Vision Engineer", ["PyTorch", "OpenCV", "C++", "CUDA"]),
         ("Werkstudent KI", ["Python", "PyTorch", "Hugging Face"]),
         ("Lead AI Engineer", ["Python", "LLM", "Azure", "AI agents", "FastAPI"])]
GERMAN = ["Sehr gute Deutschkenntnisse (C1).", "Gute Deutschkenntnisse.", "German is a plus.", "Fließend Deutsch und Englisch."]
REMOTE = ["Hybrides Arbeiten mit 2 Tagen Homeoffice.", "100% remote in Deutschland.", "Arbeit vor Ort im Büro.", ""]


def ad(role: str, skills: list[str], rng: random.Random) -> str:
    must = rng.sample(skills, k=min(len(skills), rng.randint(3, 5)))
    nice = [s for s in skills if s not in must]
    salary = f"\n{rng.randint(55, 75)}.000 - {rng.randint(80, 110)}.000 € im Jahr." if rng.random() < 0.3 else ""
    return (f"Deine Aufgaben\n- Du entwickelst und betreibst {role}-Lösungen.\n"
            f"Dein Profil\n- Erfahrung mit {', '.join(must)}\n- {rng.randint(1, 6)}+ Jahre Erfahrung\n- {rng.choice(GERMAN)}\n"
            f"Von Vorteil\n- {', '.join(nice) or 'Neugier'}\nWir bieten\n- {rng.choice(REMOTE)}{salary}")


def main() -> None:
    db = get_db()
    rng = random.Random(1)
    cfg = {"missed_runs_to_close": 2, "unverified_ttl_days": 21, "max_age_days": 90}
    now = utcnow()
    n = 0
    for i in range(60):
        role, skills = rng.choice(ROLES)
        company = f"Demo{i % 25} GmbH"
        city = rng.choice(CITIES)
        text = ad(role, skills, rng)
        src = rng.choice(["greenhouse", "lever", "adzuna", "arbeitnow"])
        ingest(db, Posting(source=src, source_id=f"demo-{i}", scope="demo", title=f"{role} (m/w/d)", company=company,
                           city=city, description=text), now=now, verifiable=True, freshness_cfg=cfg)
        if rng.random() < 0.3:   # the same job again from an aggregator -> must merge
            ingest(db, Posting(source="adzuna", source_id=f"dup-{i}", title=role, company=company.replace(" GmbH", ""),
                               city=city, description=text[:200], description_is_snippet=True),
                   now=now, verifiable=True, freshness_cfg=cfg)
        n += 1
    print(f"seeded {n} synthetic postings -> {db.jobs.count_documents({})} unique jobs in {db.name}")


if __name__ == "__main__":
    main()
