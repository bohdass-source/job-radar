"""Mode 4 — guided discovery for someone who does not yet know what fits.

Sequential, concrete questions with numbered answers:
  1. what you enjoy  2. your background  3. programming  4. maths/statistics
  5-6. German / English  7. city + radius  8. home office  9. salary range
  10. restrictions.
Then: role families scored against the answers, each shown with LIVE market
facts (open jobs after your filters, top skills, how many need C1 German,
median stated salary, share of junior/entry jobs). You pick a role, tick the
skills you already have from that role's top list, and get your jobs in the
three buckets plus a learning list ordered by market demand.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from statistics import median
from typing import Any, Callable

from pymongo.database import Database

from ..config import load_yaml
from ..match.profile import Profile, RemotePref
from . import filters as F
from .engine import live_jobs

Ask = Callable[[str, list[str], bool], str]   # (question, options, multi) -> raw answer

LIKES = [("build_models", "обучать модели и разбираться, как они работают"),
         ("products_llm", "делать продукты на ChatGPT-подобных моделях (чат-боты, ассистенты)"),
         ("analyse", "анализировать данные и находить выводы"),
         ("infra", "настраивать инфраструктуру, облако, автоматизировать выкладку"),
         ("research", "исследовать новые методы, читать статьи"),
         ("vision_hw", "камеры, роботы, автомобили, «железо»"),
         ("consult", "общаться с клиентами и внедрять ИИ в компании"),
         ("automation", "автоматизировать рутинные процессы")]
BACKGROUNDS = [("developer", "разработчик"), ("analyst", "аналитик / работа с данными"),
               ("student_it", "студент или выпускник ИТ/математики"), ("ml", "уже работаю с ML"),
               ("other", "другая сфера, хочу перейти")]
LEVELS = ["none", "A1", "A2", "B1", "B2", "C1", "C2"]


@lru_cache
def roles() -> dict[str, dict[str, Any]]:
    return (load_yaml("roles.yaml") or {}).get("roles", {})


def role_of(job: dict[str, Any]) -> list[str]:
    title = job.get("title", "")
    return [rid for rid, r in roles().items() if re.search(r["title"], title, re.I)]


@dataclass
class RoleFacts:
    role_id: str
    name: str
    what: str
    affinity: float
    jobs: int
    top_skills: list[tuple[str, int]]
    german_c1_share: float
    entry_share: float
    salary_median: tuple[int | None, int | None]
    jobs_any_language: int = 0


def market_facts(jobs: list[dict[str, Any]], role_id: str) -> tuple[int, list[tuple[str, int]], float, float, tuple]:
    mine = [j for j in jobs if role_id in role_of(j)]
    skills: Counter[str] = Counter()
    c1 = entry = 0
    lo_list, hi_list = [], []
    for j in mine:
        e = j.get("extraction") or {}
        skills.update(e.get("skills_required", []))
        c1 += e.get("german_level") in ("C1", "C2")
        entry += e.get("seniority") in ("intern", "junior") or bool((j.get("attrs") or {}).get("career_changer_ok"))
        lo, hi = F.yearly_salary(j)
        if lo:
            lo_list.append(lo)
        if hi:
            hi_list.append(hi)
    n = len(mine) or 1
    sal = (int(median(lo_list)) if lo_list else None, int(median(hi_list)) if hi_list else None)
    return len(mine), skills.most_common(10), c1 / n, entry / n, sal


def _pick(raw: str, n: int) -> list[int]:
    return [int(x) - 1 for x in re.findall(r"\d+", raw) if 1 <= int(x) <= n]


def run_discovery(db: Database, ask: Ask) -> tuple[Profile, F.SearchFilters, str, list[RoleFacts]]:
    """Returns (profile, filters restricted to the chosen role, intro text, all role facts)."""
    likes_idx = _pick(ask("1/10. Что из этого вам интереснее всего? (можно несколько)", [t for _, t in LIKES], True), len(LIKES))
    likes = [LIKES[i][0] for i in likes_idx] or ["build_models"]
    bg_idx = _pick(ask("2/10. Ваш опыт сейчас?", [t for _, t in BACKGROUNDS], False), len(BACKGROUNDS))
    background = BACKGROUNDS[bg_idx[0]][0] if bg_idx else "other"
    prog = _pick(ask("3/10. Программирование?", ["не программирую", "немного (курсы, скрипты)",
                                                 "уверенно (Python или другой язык)"], False), 3)
    prog_lvl = prog[0] if prog else 0
    math = _pick(ask("4/10. Математика и статистика?", ["сложно", "базовый уровень", "уверенно"], False), 3)
    math_lvl = math[0] if math else 1
    de = _pick(ask("5/10. Немецкий?", LEVELS, False), len(LEVELS))
    en = _pick(ask("6/10. Английский?", LEVELS, False), len(LEVELS))
    cities = [c.strip() for c in re.split(r"[,;]", ask("7/10. В каких городах искать? (через запятую, пусто = вся Германия)", [], False)) if c.strip()]
    radius = 0
    if cities:
        r = re.findall(r"\d+", ask("   Радиус вокруг этих городов в км? (0 = только сами города)", [], False))
        radius = int(r[0]) if r else 0
    wp = _pick(ask("8/10. Домашний офис?", ["только удалённо", "гибрид или удалённо", "всё равно, можно офис"], False), 3)
    workplace = [["remote"], ["remote", "hybrid"], []][wp[0] if wp else 2]
    sal = [int(x.replace(" ", "").replace(".", "")) for x in
           re.findall(r"\d[\d .]*\d", ask("9/10. Диапазон зарплаты в год, € (например 50000-70000, пусто = не важно)", [], False))]
    restr = _pick(ask("10/10. Ограничения (можно несколько, пусто = нет):",
                      ["без кадровых агентств / Zeitarbeit", "только полная занятость", "только частичная занятость",
                       "только бессрочный договор", "подходит без профильного опыта (Quereinstieg)",
                       "без стажировок и мини-джобов"], True), 6)

    german = LEVELS[de[0]] if de else "none"
    english = LEVELS[en[0]] if en else "B1"
    filters = F.SearchFilters(
        cities=cities, radius_km=radius, workplace=workplace,
        salary_min=sal[0] if sal else None, salary_max=sal[1] if len(sal) > 1 else None,
        german_max=german, english_max=english, no_agency=0 in restr,
        fulltime=True if 1 in restr else (False if 2 in restr else None), permanent_only=3 in restr,
        career_changer_ok=4 in restr, exclude_internships=5 in restr, sort="fit",
    )

    live = live_jobs(db)
    jobs, _ = F.apply(live, filters)
    # Market facts ignore the language filter, so "needs C1 German in X%" is informative.
    jobs_any_lang, _ = F.apply(live, filters.model_copy(update={"german_max": None, "english_max": None}))
    facts: list[RoleFacts] = []
    for rid, r in roles().items():
        aff = sum(r["likes"].get(l, 0) for l in likes) + r["backgrounds"].get(background, 0)
        if rid in ("ml_engineer", "mlops", "llm_engineer", "nlp", "cv_robotics") and prog_lvl == 0:
            aff -= 2      # these roles need programming from day one
        if rid in ("research", "data_scientist", "ml_engineer") and math_lvl == 0:
            aff -= 2
        n = len([j for j in jobs if rid in role_of(j)])
        n_any, top, c1, entry, salary = market_facts(jobs_any_lang, rid)
        facts.append(RoleFacts(rid, r["name"], r["what"], aff, n, top, c1, entry, salary, n_any))
    # Affinity first; market size breaks ties and sinks roles with no open jobs.
    facts.sort(key=lambda f: (-(f.affinity + (0.5 if f.jobs >= 20 else 0) - (3 if f.jobs == 0 else 0)), -f.jobs))

    options = []
    for f in facts[:4]:
        sal_txt = f"{f.salary_median[0]:,}–{f.salary_median[1]:,} €".replace(",", " ") if all(f.salary_median) else "зарплата редко указана"
        options.append(f"{f.name}: {f.what} Сейчас {f.jobs} вакансий по вашим фильтрам (без учёта языка — {f.jobs_any_language}, "
                       f"из них немецкий C1+ требуют {f.german_c1_share:.0%}); "
                       f"для начинающих {f.entry_share:.0%}; {sal_txt}. Чаще всего просят: "
                       + ", ".join(s for s, _ in f.top_skills[:6]))
    choice = _pick(ask("Вам больше всего подходят эти направления. Какое посмотреть?", options, False), len(options))
    chosen = facts[:4][choice[0] if choice else 0]

    top_names = [s for s, _ in chosen.top_skills]
    have_idx = _pick(ask(f"Какие из самых востребованных навыков для «{chosen.name}» у вас уже есть? (номера, пусто = никаких)",
                         top_names, True), len(top_names)) if top_names else []
    skills = {top_names[i]: 2 for i in have_idx}
    seniority = "junior" if background in ("other", "student_it") or prog_lvl < 2 else "mid"
    profile = Profile(skills=skills, seniority=seniority, german_level=german, english_level=english, cities=cities,
                      relocate=False, remote_pref={("remote",): RemotePref.REMOTE_ONLY}.get(tuple(workplace),
                      RemotePref.HYBRID_OR_REMOTE if workplace else RemotePref.ANY),
                      min_salary_year=filters.salary_min, target_roles=[chosen.name],
                      summary=f"{chosen.name}. Interests: {', '.join(likes)}. Background: {background}.",
                      source="discover")
    learn = [s for s in top_names if s not in skills]
    intro = (f"Направление: {chosen.name}. {chosen.what}\n"
             f"Что выучить в первую очередь (по частоте в вакансиях): {', '.join(learn[:6]) or '—'}.")
    filters = filters.model_copy(update={"title_regex": roles()[chosen.role_id]["title"]})
    return profile, filters, intro, facts
