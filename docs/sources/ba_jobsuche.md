---
name: ba_jobsuche
title: "Bundesagentur für Arbeit — Jobsuche API"
terms_urls:
  - https://www.arbeitsagentur.de/nutzungsbedingungen
  - https://www.arbeitsagentur.de/impressum
  - https://github.com/bundesAPI/jobsuche-api
checked: 2026-11-16
rate_limit: "not published"
min_interval_s: 2.0
max_requests_per_run: 2500
status: needs_review   # approved | needs_review | blocked
verifiable: true
---

# Bundesagentur für Arbeit — Jobsuche API

- The endpoint is documented by the community project bundesAPI. Its README says the BA itself offers no official API.
- Nutzungsbedingungen §2a(3) (version of 10.11.2026) forbid robots, web spiders and unauthorised interfaces used to read portal content for data collection and analysis.
- Impressum: content may be used for information only; reuse, copying or storage in electronic systems needs prior permission and a source citation.
- **Default:** disabled (`status: needs_review`, `enabled: false`). Whether to use this source is a decision for
  whoever runs job-radar, after reading the clauses above. The cleanest route is written permission from the BA
  (hotline@service.arbeitsagentur.de).
- **To enable on your machine**, create `config/sources.local.yaml` (git-ignored):

  ```yaml
  sources: {ba_jobsuche: {enabled: true}}
  terms_overrides: {ba_jobsuche: approved}
  ```

  `collect` then logs that the terms status was overridden locally.
- **What the adapter guarantees when enabled:** 2 s pause between requests, a per-run request budget, detail pages
  only for new AI/ML ads, no captcha or bot-protection bypass (a 403 or non-JSON answer stops the run), no
  republication of ads, and aggregates cite "BUNDESAGENTUR FÜR ARBEIT (BA)" as the Impressum asks.
