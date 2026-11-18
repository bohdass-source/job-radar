"""Guided interview that builds (or completes) your profile.

Questions live in MongoDB (`questions`, seeded from config/profile_questions.yaml).
Skill questions are concrete: they list the skills of one area ordered by how
often the collected jobs require them, then ask a level for each skill you
picked, then offer the market's most demanded skills you have not mentioned.
`ask` is injected so the engine can be tested without a terminal.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from pymongo.database import Database

from ..config import load_yaml
from ..skills import all_skills, canonical, category_of
from .profile import Profile, merge_into
from ..i18n import question_text, t

Ask = Callable[[dict[str, Any], list[str]], str]


def seed_questions(db: Database, overwrite: bool = False) -> int:
    count = 0
    for q in load_yaml("profile_questions.yaml").get("questions", []):
        if overwrite or not db.questions.find_one({"qid": q["qid"]}):
            db.questions.replace_one({"qid": q["qid"]}, q, upsert=True)
            count += 1
    return count


def market_demand(db: Database) -> Counter[str]:
    demand: Counter[str] = Counter()
    for job in db.jobs.find({"status": {"$in": ["open", "unverified"]}, "extraction": {"$ne": None}},
                            {"extraction.skills_required": 1, "extraction.skills_nice_to_have": 1}):
        ext = job.get("extraction") or {}
        demand.update(ext.get("skills_required", []))
        demand.update({s: 0.5 for s in ext.get("skills_nice_to_have", [])})  # type: ignore[arg-type]
    return demand


def _condition_ok(cond: str | None, answers: dict[str, Any]) -> bool:
    if not cond:
        return True
    left, op, right = cond.split()
    value = str(answers.get(left, ""))
    return value != right if op == "!=" else value == right


def _split_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def _pick(raw: str, options: list[str]) -> list[str]:
    """Accept numbers ("1,3"), names, or a mix."""
    picked = []
    for token in _split_list(raw):
        if token.isdigit() and 1 <= int(token) <= len(options):
            picked.append(options[int(token) - 1])
        else:
            match = next((o for o in options if o.lower() == token.lower()), token)
            picked.append(match)
    return picked


def run_interview(db: Database, ask: Ask, base: Profile | None = None, only_missing: bool = False) -> Profile:
    if not db.questions.count_documents({}):
        seed_questions(db)
    demand = market_demand(db)
    profile = base or Profile()
    answers: dict[str, Any] = {}
    selected: list[str] = list(profile.skills) if base else []
    updates: dict[str, Any] = {"skills": {}}

    for q in db.questions.find({}).sort("order", 1):
        if not _condition_ok(q.get("ask_if"), {**profile.model_dump(mode="json"), **answers}):
            continue
        field = q.get("sets")
        if only_missing and field not in ("skills",) and getattr(profile, field, None) not in (None, "", [], 0, "unknown"):
            continue
        qtype = q["type"]

        if qtype == "skills":
            cats = q["category"] if isinstance(q["category"], list) else [q["category"]]
            options = sorted((s for s in all_skills() if category_of(s) in cats), key=lambda s: -demand[s])
            labels = [f"{s} ({int(demand[s])})" if demand[s] else s for s in options]
            raw = ask(q, labels)
            chosen = [canonical(x.split(" (")[0]) or x.split(" (")[0] for x in _pick(raw, labels)]
            selected += [c for c in chosen if c not in selected]
            continue

        if qtype == "skill_level":
            for skill in selected:
                if skill in profile.skills and only_missing:
                    continue
                raw = ask({**q, "text": question_text(q).format(skill=skill)}, ["1", "2", "3"])
                level = int(raw) if raw.strip() in ("1", "2", "3") else 2
                updates["skills"][skill] = level
            continue

        if qtype == "market_gaps":
            have = set(selected) | set(updates["skills"])
            gaps = [s for s, _ in demand.most_common() if s not in have][: int(q.get("top_n", 12))]
            if not gaps:
                continue
            total = max(1, db.jobs.count_documents({"status": {"$in": ["open", "unverified"]}, "extraction": {"$ne": None}}))
            labels = [t("i.pct_jobs", skill=s, pct=round(100 * demand[s] / total)) for s in gaps]
            raw = ask(q, labels)
            for label in _pick(raw, labels):
                skill = label.split(" — ")[0]
                lvl = ask({"qid": f"level_{skill}", "type": "single",
                           "text": t("i.level", skill=skill)}, ["1", "2", "3"])
                updates["skills"][skill] = int(lvl) if lvl.strip() in ("1", "2", "3") else 1
            continue

        options = [str(o) for o in q.get("options", [])]
        raw = ask(q, options).strip()
        if qtype == "single":
            value: Any = _pick(raw, options)[0] if raw else None
        elif qtype == "multi":
            value = _pick(raw, options)
        elif qtype == "bool":
            value = raw.lower() in ("y", "yes", "да", "д", "1", "true", "ja")
        elif qtype == "number":
            digits = "".join(ch for ch in raw if ch.isdigit())
            value = int(digits) if digits else None
            if field == "min_salary_year" and not value:
                value = None
        else:
            value = _split_list(raw) if field in ("cities", "learning", "exclude_companies") else raw
        if field == "learning" and value:
            value = [canonical(v) or v for v in value]
        answers[field] = value
        if value not in (None, "", []):
            updates[field] = value

    for skill in selected:
        updates["skills"].setdefault(skill, profile.skills.get(skill, 2))
    profile = merge_into(profile, updates)
    profile.source = "interview" if base is None else f"{profile.source}+interview"
    return profile


def terminal_ask(q: dict[str, Any], options: list[str]) -> str:
    print(f"\n{question_text(q)}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    hint = t("i.hint_many") if q.get("type") in ("multi", "skills", "market_gaps") else ""
    return input(f"> {hint} ").strip() if hint else input("> ").strip()


def cv_draft_markdown(profile: Profile) -> str:
    by_cat: dict[str, list[str]] = {}
    for skill, level in sorted(profile.skills.items(), key=lambda kv: -kv[1]):
        by_cat.setdefault(category_of(skill), []).append(skill + (" ★" if level == 3 else ""))
    lines = ["# Lebenslauf / CV — Entwurf", "",
             t("i.cv_note"), "",
             "## Profil", profile.summary or t("i.cv_profile_todo"), "",
             f"**Zielrollen:** {', '.join(profile.target_roles) or '—'}  ",
             f"**Erfahrung:** {profile.years_experience} Jahre, Level {profile.seniority}  ",
             f"**Sprachen:** Deutsch {profile.german_level}, Englisch {profile.english_level}", "",
             "## Kenntnisse"]
    for cat, skills in by_cat.items():
        lines.append(f"- **{cat}:** {', '.join(skills)}")
    if profile.learning:
        lines += ["", f"**Aktuell in Weiterbildung:** {', '.join(profile.learning)}"]
    lines += ["", "## Berufserfahrung / Projekte", t("i.cv_exp_todo")]
    return "\n".join(lines) + "\n"
