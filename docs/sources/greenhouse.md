---
name: greenhouse
title: "Greenhouse Job Board API"
terms_urls:
  - https://developers.greenhouse.io/job-board.html
checked: 2026-11-16
rate_limit: "none stated for GET"
min_interval_s: 2.0
max_requests_per_run: 300
status: approved   # approved | needs_review | blocked
verifiable: true
---

# Greenhouse Job Board API

- Public, documented JSON endpoint; no auth for GET. Job board data is described as public.
- No terms, licence or API terms found on the docs pages.
- Full descriptions with `content=true`; single-job endpoint returns 404 once a job is unpublished (used by `refresh`).
