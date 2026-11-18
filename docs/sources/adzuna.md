---
name: adzuna
title: "Adzuna API (Germany)"
terms_urls:
  - https://developer.adzuna.com/docs/terms_of_service
  - https://developer.adzuna.com/docs/search
checked: 2026-11-16
rate_limit: "shown in your Adzuna account after signup; not published"
min_interval_s: 3.0
max_requests_per_run: 200
status: approved   # approved | needs_review | blocked
verifiable: true
---

# Adzuna API (Germany)

- Official API, free key (app_id + app_key) on signup at developer.adzuna.com.
- Permitted uses include personal job search and personal/academic research. Commercial analysis, resale and lead generation need Adzuna's written agreement.
- Attribution: wherever vacancy or salary data is published, name "The Adzuna API" as the source and link to adzuna.co.uk (the README and the public report do this).
- Only a snippet of the description is returned. job-radar does not open `redirect_url` pages.
- On termination, data acquired from Adzuna must be removed.
- Freshness: no "closed" field; a job missing from two complete runs is treated as closed.
