---
name: smartrecruiters
title: "SmartRecruiters Posting API"
terms_urls:
  - https://developers.smartrecruiters.com/docs/posting-api
  - https://developers.smartrecruiters.com/docs/rate-limiting
checked: 2026-11-16
rate_limit: "10 requests/s, 8 concurrent (documented)"
min_interval_s: 1.0
max_requests_per_run: 400
status: approved   # approved | needs_review | blocked
verifiable: true
---

# SmartRecruiters Posting API

- Documented public posting API. The docs mention API key / OAuth support; list calls in the examples run without a key. Confirm on the first call.
- No terms found on the developer hub.
- The list has no description; job-radar fetches the detail only for AI/ML-relevant titles to save requests.
