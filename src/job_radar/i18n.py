"""User-facing text in English and Russian.

Language: `--lang` on the command line, else JOB_RADAR_LANG (env or .env), else English.
Data stays language-neutral (bucket ids, filter keys); only rendering goes through `t()`.
"""

from __future__ import annotations

import os

LANGS = ("en", "ru")
_lang: str | None = None

M: dict[str, tuple[str, str]] = {
    # buckets
    "bucket.apply": ("APPLY", "ПОДХОДИТ"),
    "bucket.stretch": ("STRETCH", "ДОРАБОТАТЬ"),
    "bucket.reject": ("NO FIT", "НЕ ПОДХОДИТ"),
    # matcher reasons
    "lang.de": ("German", "Deutsch"),
    "lang.en": ("English", "Englisch"),
    "m.lang_unknown": ("{name}: {need} required, your level is not in the profile",
                       "{name}: требуется {need}, ваш уровень не указан в профиле"),
    "m.lang_inferred": (" (not stated; the ad is written in it)", " (не указан явно, объявление на этом языке)"),
    "m.lang_block": ("{name}: {need} required, you have {have}", "{name}: требуется {need}, у вас {have}"),
    "m.lang_gap": ("{name}: needs {need}{suffix}, you have {have}", "{name}: нужно {need}{suffix}, у вас {have}"),
    "m.skills_unrecognised": ("no skills recognised in the ad — check it manually",
                              "навыки в объявлении не распознаны — проверьте вручную"),
    "m.remote_unknown": ("workplace not stated and you want remote only — check",
                         "формат работы не указан, вам нужен только remote — уточните"),
    "m.remote_only": ("{fmt}, you want remote only", "формат {fmt}, вам нужен только remote"),
    "m.onsite_only": ("on-site only, you want hybrid/remote", "только офис, вам нужен hybrid/remote"),
    "m.city_relocate": ("{city} — would need relocation", "город {city} — потребуется переезд"),
    "m.city_block": ("{city} is not in your list", "город {city} не в вашем списке"),
    "m.city_gap": ("{city} is not in your list, workplace not stated", "город {city} не в вашем списке, формат не указан"),
    "m.seniority_block": ("{job} level, you are {mine}", "уровень {job}, ваш {mine}"),
    "m.seniority_gap": ("{job} level is one step above yours ({mine})", "уровень {job} на ступень выше вашего ({mine})"),
    "m.internship_below": ("internship/working student is below your level", "стажировка/Werkstudent ниже вашего уровня"),
    "m.years_block": ("needs {yrs}+ years, you have {have}", "нужно {yrs}+ лет опыта, у вас {have}"),
    "m.years_gap": ("needs {yrs}+ years, you have {have}", "нужно {yrs}+ лет опыта, у вас {have}"),
    "m.salary_below": ("salary up to {amount} EUR/year is below your minimum",
                       "зарплата до {amount} EUR/год ниже вашего минимума"),
    "m.company_excluded": ("company is on your exclusion list", "компания в вашем списке исключений"),
    "m.skills_few": ("only {m} of {n} required skills", "есть только {m} из {n} обязательных навыков"),
    "m.skills_covered": ("{m} of {n} required skills covered", "покрыто {m} из {n} обязательных навыков"),
    "m.unverified": ("not verified automatically (alert e-mail / manual page) — open the link before applying",
                     "не проверено автоматически (письмо/ручная страница) — откройте ссылку перед подачей"),
    # filters: description
    "f.km": ("{km} km", "{km} км"),
    "f.workplace": ("workplace: {v}", "формат: {v}"),
    "f.salary": ("salary {lo}–{hi} €", "зарплата {lo}–{hi} €"),
    "f.german": ("German up to {v}", "немецкий до {v}"),
    "f.english": ("English up to {v}", "английский до {v}"),
    "f.seniority": ("seniority: {v}", "уровень: {v}"),
    "f.no_agency": ("no agencies", "без агентств"),
    "f.permanent": ("permanent only", "бессрочно"),
    "f.career_changer": ("career changers", "Quereinstieg"),
    "f.no_internships": ("no internships", "без стажировок"),
    "f.fulltime": ("full-time", "полная занятость"),
    "f.parttime": ("part-time", "частичная занятость"),
    "f.days": ("last {n} days", "за {n} дн."),
    "f.none": ("no filters", "без фильтров"),
    # filters: reasons a job was dropped
    "r.city": ("city/radius", "город/радиус"),
    "r.workplace": ("workplace", "формат работы"),
    "r.no_salary": ("salary not stated", "зарплата не указана"),
    "r.salary_low": ("salary below range", "зарплата ниже диапазона"),
    "r.salary_high": ("salary above range", "зарплата выше диапазона"),
    "r.german": ("German above your level", "немецкий выше вашего уровня"),
    "r.english": ("English above your level", "английский выше вашего уровня"),
    "r.seniority": ("seniority", "уровень позиции"),
    "r.internship": ("internship/mini-job", "стажировка/мини-джоб"),
    "r.agency": ("staffing agency", "кадровое агентство"),
    "r.not_fulltime": ("not full-time", "не полная занятость"),
    "r.no_parttime": ("no part-time", "нет частичной занятости"),
    "r.fixed_term": ("fixed-term contract", "срочный договор"),
    "r.not_career_changer": ("not for career changers", "не для Quereinstieg"),
    "r.old": ("published too long ago", "опубликована давно"),
    "r.other_role": ("other role", "другая роль"),
    "r.company": ("company excluded", "компания исключена"),
    "r.kw_all": ("not all keywords", "нет всех ключевых слов"),
    "r.kw_any": ("no keywords", "нет ключевых слов"),
    "r.kw_none": ("excluded word", "исключённое слово"),
    # search output
    "s.unverified": ("unverified", "не проверено"),
    "s.filters": ("Filters: {f}. Sort: {s}.", "Фильтры: {f}. Сортировка: {s}."),
    "s.counts": ("Open jobs: {live}; after filters: {kept}", "Актуальных вакансий: {live}; после фильтров: {kept}"),
    "s.dropped": ("; dropped: {d}", "; отсеяно: {d}"),
    "s.rejected": ("Not a fit for the profile: {n} (listed in the HTML report)",
                   "Не подходят по профилю: {n} (показаны в HTML-отчёте)"),
    "s.missing": ("missing: {v}", "не хватает: {v}"),
    "s.cover_note": ("for the cover letter: {v}", "в письмо: {v}"),
    "s.html_title": ("job-radar · search: {mode}", "job-radar · поиск: {mode}"),
    "s.closed_excluded": ("Closed jobs are excluded.", "Закрытые вакансии исключены."),
    "s.html_filters": ("Filters:", "Фильтры:"),
    "s.html_sort": ("Sort:", "Сортировка:"),
    "s.html_counts": ("Open: {live}; removed by filters: {d}", "Актуальных: {live}; отсеяно фильтрами: {d}"),
    "s.results": ("Results ({n})", "Результаты ({n})"),
    "s.no_fit": ("Not a fit for the profile ({n})", "Не подходят по профилю ({n})"),
    "s.show": ("Show", "Показать"),
    "s.no_jobs": ("No jobs.", "Нет вакансий."),
    "col.job": ("Job", "Вакансия"),
    "col.skills": ("Skills", "Навыки"),
    "col.skills_missing": ("Skills (orange = missing)", "Навыки (оранжевым — не хватает)"),
    "col.reasons": ("Reasons / notes", "Причины / заметки"),
    "col.status": ("Status", "Статус"),
    "col.verified": ("Verified", "Проверено"),
    # discover
    "d.q1": ("1/10. What appeals to you most? (several allowed)", "1/10. Что из этого вам интереснее всего? (можно несколько)"),
    "d.like.build_models": ("training models and understanding how they work", "обучать модели и разбираться, как они работают"),
    "d.like.products_llm": ("building products on ChatGPT-like models (chatbots, assistants)",
                            "делать продукты на ChatGPT-подобных моделях (чат-боты, ассистенты)"),
    "d.like.analyse": ("analysing data and drawing conclusions", "анализировать данные и находить выводы"),
    "d.like.infra": ("infrastructure, cloud, automated deployment", "настраивать инфраструктуру, облако, автоматизировать выкладку"),
    "d.like.research": ("researching new methods, reading papers", "исследовать новые методы, читать статьи"),
    "d.like.vision_hw": ("cameras, robots, cars, hardware", "камеры, роботы, автомобили, «железо»"),
    "d.like.consult": ("working with clients and bringing AI into companies", "общаться с клиентами и внедрять ИИ в компании"),
    "d.like.automation": ("automating routine processes", "автоматизировать рутинные процессы"),
    "d.q2": ("2/10. Your background?", "2/10. Ваш опыт сейчас?"),
    "d.bg.developer": ("software developer", "разработчик"),
    "d.bg.analyst": ("analyst / working with data", "аналитик / работа с данными"),
    "d.bg.student_it": ("student or graduate in IT/maths", "студент или выпускник ИТ/математики"),
    "d.bg.ml": ("already working with ML", "уже работаю с ML"),
    "d.bg.other": ("another field, want to switch", "другая сфера, хочу перейти"),
    "d.q3": ("3/10. Programming?", "3/10. Программирование?"),
    "d.prog": ("I don't program|a little (courses, scripts)|confidently (Python or another language)",
               "не программирую|немного (курсы, скрипты)|уверенно (Python или другой язык)"),
    "d.q4": ("4/10. Maths and statistics?", "4/10. Математика и статистика?"),
    "d.math": ("hard for me|basic|confident", "сложно|базовый уровень|уверенно"),
    "d.q5": ("5/10. German?", "5/10. Немецкий?"),
    "d.q6": ("6/10. English?", "6/10. Английский?"),
    "d.q7": ("7/10. Which cities? (comma-separated, empty = all of Germany)",
             "7/10. В каких городах искать? (через запятую, пусто = вся Германия)"),
    "d.q7r": ("   Radius around these cities in km? (0 = the cities only)",
              "   Радиус вокруг этих городов в км? (0 = только сами города)"),
    "d.q8": ("8/10. Home office?", "8/10. Домашний офис?"),
    "d.wp": ("remote only|hybrid or remote|any, office is fine", "только удалённо|гибрид или удалённо|всё равно, можно офис"),
    "d.q9": ("9/10. Salary range per year, € (e.g. 50000-70000, empty = any)",
             "9/10. Диапазон зарплаты в год, € (например 50000-70000, пусто = не важно)"),
    "d.q10": ("10/10. Restrictions (several allowed, empty = none):", "10/10. Ограничения (можно несколько, пусто = нет):"),
    "d.restr": ("no staffing agencies / Zeitarbeit|full-time only|part-time only|permanent contracts only|"
                "open to career changers (Quereinstieg)|no internships or mini-jobs",
                "без кадровых агентств / Zeitarbeit|только полная занятость|только частичная занятость|"
                "только бессрочный договор|подходит без профильного опыта (Quereinstieg)|без стажировок и мини-джобов"),
    "d.salary_rare": ("salary rarely stated", "зарплата редко указана"),
    "d.role_option": ("{name}: {what} {jobs} jobs match your filters ({any_lang} ignoring language, {c1} of them need C1+ "
                      "German); entry-level {entry}; {salary}. Most requested: {skills}",
                      "{name}: {what} Сейчас {jobs} вакансий по вашим фильтрам (без учёта языка — {any_lang}, из них "
                      "немецкий C1+ требуют {c1}); для начинающих {entry}; {salary}. Чаще всего просят: {skills}"),
    "d.pick_role": ("These directions fit you best. Which one to explore?",
                    "Вам больше всего подходят эти направления. Какое посмотреть?"),
    "d.have_skills": ("Which of the most requested skills for “{role}” do you already have? (numbers, empty = none)",
                      "Какие из самых востребованных навыков для «{role}» у вас уже есть? (номера, пусто = никаких)"),
    "d.intro": ("Direction: {role}. {what}\nLearn first (by frequency in ads): {learn}.",
                "Направление: {role}. {what}\nЧто выучить в первую очередь (по частоте в вакансиях): {learn}."),
    # CLI
    "c.pick_many": (" (numbers, comma-separated)", " (номера через запятую)"),
    "c.pick_one": (" (number)", " (номер)"),
    "c.portfolio": ("Portfolio: {n} projects. Skills with evidence: {v}", "Портфолио: {n} проектов. Навыки с доказательствами: {v}"),
    "c.q_written": ("Questionnaire: {path}\nFill it in and run: job-radar find questionnaire {path}",
                    "Анкета: {path}\nЗаполните её и запустите: job-radar find questionnaire {path}"),
    "c.q_done": ("Questionnaire processed ({how}): {n} skills, German {de}, English {en}.",
                 "Анкета обработана ({how}): {n} навыков, немецкий {de}, английский {en}."),
    "c.q_how_llm": ("by the LLM", "ИИ"),
    "c.q_how_rules": ("by rules, no LLM available", "правилами, LLM недоступна"),
    "c.q_other": (" Other restrictions: {v}", " Другие ограничения: {v}"),
    "c.query": ("Query: “{q}”.", "Поиск: «{q}»."),
    "c.hint_german": ("Hint: pass --german <level>, otherwise German requirements are not checked.",
                      "Подсказка: укажите --german <уровень>, иначе требования к немецкому не проверяются."),
    "c.profile_saved": ("profile saved (used by match, report, digest)", "профиль сохранён (используется в match, report, digest)"),
    "c.job_closed": ("  (job closed)", "  (вакансия закрыта)"),
    "c.sources": ("Sources: ", "Источники: "),
    # digest
    "g.missing": ("Missing: {v}.", "Не хватает: {v}."),
    "g.intro": ("New open jobs since {date}.", "Новые актуальные вакансии с {date}."),
    "g.apply": ("Apply now", "Подходят — можно подаваться"),
    "g.stretch": ("Stretch", "Доработать"),
    "g.footer": ("Closed jobs are excluded. job-radar", "Закрытые вакансии исключены. job-radar"),
    "g.subject": ("job-radar: {a} to apply, {s} stretch", "job-radar: {a} подходят, {s} доработать"),
    # rag
    "a.none": ("No matching open jobs in the index.", "Нет подходящих актуальных вакансий в индексе."),
    "a.no_llm": ("LLM gateway is not configured; these are the retrieved fragments:",
                 "LLM gateway не настроен; вот найденные фрагменты:"),
}


def set_lang(lang: str | None) -> None:
    global _lang
    _lang = lang if lang in LANGS else None


def get_lang() -> str:
    if _lang:
        return _lang
    from .config import settings  # loads .env

    settings()
    env = os.environ.get("JOB_RADAR_LANG", "en").lower()
    return env if env in LANGS else "en"


def t(key: str, **kw: object) -> str:
    en, ru = M[key]
    text = ru if get_lang() == "ru" else en
    return text.format(**kw) if kw else text


def options(key: str) -> list[str]:
    """Pipe-separated option lists ("a|b|c")."""
    return t(key).split("|")


# HTML reports
M.update({
    "h.no_data": ("no data", "нет данных"),
    "h.kpi_live": ("Open jobs", "Актуальных вакансий"),
    "h.kpi_total": ("Unique jobs seen", "Всего уникальных за всё время"),
    "h.kpi_records": ("Source records", "Записей из источников"),
    "h.kpi_salary": ("Median stated salary", "Медиана зарплаты (указана)"),
    "h.market": ("Market", "Рынок"),
    "h.market_note": ("Status of all jobs: {status}. Ads with only a text snippet: {snip}% (skills are extracted "
                      "incompletely for them). Salary stated in {n} ads.",
                      "Статусы всех вакансий: {status}. Доля объявлений, где доступен только фрагмент текста: {snip}% — "
                      "для них навыки извлекаются неполно. Зарплата указана в {n} объявлениях."),
    "h.req_skills": ("Required skills (share of jobs)", "Обязательные навыки (доля вакансий)"),
    "h.nice_skills": ("Nice-to-have skills", "Желательные навыки"),
    "h.categories": ("Skill categories", "Категории навыков"),
    "h.german": ("German", "Немецкий"),
    "h.english": ("English", "Английский"),
    "h.workplace": ("Workplace", "Формат работы"),
    "h.seniority": ("Seniority", "Уровень"),
    "h.cities": ("Cities", "Города"),
    "h.ad_lang": ("Language of the ad", "Язык объявления"),
    "h.sources": ("Sources", "Источники"),
    "h.sources_note": ("“Only from this source” = jobs no other source had, after cross-source merging.",
                       "«Уникальных» — вакансии, которые дал только этот источник (после склейки дублей между источниками)."),
    "h.col_source": ("Source", "Источник"),
    "h.col_jobs": ("Jobs", "Вакансий"),
    "h.col_unique": ("Only from this source", "Уникальных"),
    "h.col_open": ("Open", "Актуальных"),
    "h.col_last_run": ("Last run (UTC)", "Последний сбор (UTC)"),
    "h.data_sources": ("Data sources: ", "Источники данных: "),
    "h.public_title": ("job-radar — AI/ML job market in Germany", "job-radar — рынок AI/ML-вакансий в Германии"),
    "h.public_sub": ("Generated {ts} UTC. Aggregates only; closed and expired jobs are excluded.",
                     "Сформировано {ts} UTC. Только агрегаты; закрытые и устаревшие вакансии исключены."),
    "h.public_footer": ("Alert e-mails and manually sent pages are not part of these aggregates.",
                        "Письма-уведомления и вручную отправленные страницы в агрегаты не входят."),
    "h.none_now": ("No such jobs right now.", "Сейчас таких вакансий нет."),
    "h.score": ("Score", "Score"),
    "h.private_title": ("job-radar — my jobs", "job-radar — мои вакансии"),
    "h.private_sub": ("Generated {ts} UTC. Closed and expired jobs never appear here. Open the link before applying: "
                      "“unverified” means the job came from an alert e-mail or a manual page.",
                      "Сформировано {ts} UTC. Закрытые и устаревшие вакансии сюда не попадают. Перед подачей откройте "
                      "ссылку: статус «не проверено» значит, что вакансия пришла из письма или вручную."),
    "h.nav_apply": ("Apply ({n})", "Подходят ({n})"),
    "h.nav_stretch": ("Stretch ({n})", "Доработать ({n})"),
    "h.nav_reject": ("No fit ({n})", "Не подходят ({n})"),
    "h.nav_tracker": ("My applications ({n})", "Мои заявки ({n})"),
    "h.apply_title": ("Apply — ready to apply", "Подходят — можно подаваться"),
    "h.stretch_title": ("Stretch — apply with an honest note, or learn the skill first",
                        "Доработать — подаваться с честной пометкой или подтянуть навык"),
    "h.most_missing": ("Most often missing", "Чего чаще всего не хватает"),
    "h.reject_title": ("No fit — filtered out", "Не подходят — отсеяны"),
    "h.main_reasons": ("Main reasons", "Главные причины"),
    "h.show_rejected": ("Show filtered-out jobs ({n})", "Показать отсеянные ({n})"),
    "h.tracker": ("My applications", "Мои заявки"),
    "h.col_my_status": ("My status", "Мой статус"),
    "h.col_job_now": ("Job now", "Вакансия сейчас"),
    "h.col_updated": ("Updated", "Обновлено"),
    "h.col_note": ("Note", "Заметка"),
    "h.private_footer": ("Personal report: stored only in data/private/.", "Личный отчёт: хранится только в data/private/."),
})


# interview + questionnaire
M.update({
    "i.pct_jobs": ("{skill} — {pct}% of jobs", "{skill} — {pct}% вакансий"),
    "i.level": ("{skill}: 1 — basics, 2 — projects, 3 — production", "{skill}: 1 — основы, 2 — проекты, 3 — продакшен"),
    "i.hint_many": (" (numbers or names, comma-separated)", " (номера или названия через запятую)"),
    "i.cv_note": ("_Draft assembled by job-radar from your answers. Stored only in data/private/._",
                  "_Черновик собран job-radar из ваших ответов. Хранится только в data/private/._"),
    "i.cv_profile_todo": ("_(write 2-3 sentences about yourself)_", "_(опишите 2-3 предложения о себе)_"),
    "i.cv_exp_todo": ("_(fill in: company, period, tasks, results)_", "_(заполните: компания, период, задачи, результаты)_"),
    "q.title": ("# job-radar questionnaire", "# Анкета job-radar"),
    "q.intro": ("Answer in your own words under each question, in English, German or Russian.",
                "Отвечайте своими словами под каждым вопросом. Можно по-русски, по-немецки или по-английски."),
    "q.private": ("The file stays on your machine (data/private/).", "Файл хранится только у вас (data/private/)."),
    "q.skills_suffix": (" — list them and give a level (basics / projects / production):",
                        " — перечислите и укажите уровень (основы / проекты / продакшен):"),
    "q.radius_km": ("How far from these cities are you willing to commute (km)?",
                    "Насколько далеко от этих городов вы готовы ездить (км)?"),
    "q.workplace": ("Workplace: remote only / hybrid / office — what works for you?",
                    "Формат: только удалённо / гибрид / офис — что подходит?"),
    "q.salary": ("Desired yearly gross salary range (e.g. 60 000–80 000)?",
                 "Желаемый диапазон зарплаты в год, брутто (например 60 000–80 000)?"),
    "q.restrictions": ("Restrictions: no staffing agencies? full-time or part-time only? permanent contracts only? "
                       "do you need a visa? no weekends? anything else?",
                       "Ограничения: без кадровых агентств? только полная/частичная занятость? только бессрочный договор? "
                       "нужна ли виза? не работаете в выходные? что-то ещё?"),
})


def question_text(q: dict) -> str:
    """Interview/questionnaire wording in the current language, with the YAML as fallback for old DB rows."""
    if get_lang() == "ru":
        return q["text"]
    if q.get("text_en"):
        return q["text_en"]
    from .config import load_yaml

    for row in load_yaml("profile_questions.yaml").get("questions", []):
        if row.get("qid") == q.get("qid") and row.get("text_en"):
            return row["text_en"]
    return q["text"]
