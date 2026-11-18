---
name: personio
title: "Personio public XML feed"
terms_urls:
  - https://developer.personio.de/docs/retrieving-open-job-positions
  - https://developer.personio.de/docs/tos-api-security-1
checked: 2026-11-16
rate_limit: "not stated"
min_interval_s: 2.0
max_requests_per_run: 200
status: approved   # approved | needs_review | blocked
verifiable: true
---

# Personio public XML feed

- Public XML of open positions; no auth; TLS 1.2+.
- The only terms found are Personio's partner Marketplace ToS / API policy for developer accounts; whether they cover reading the public feed is not stated.
- No remote flag and no apply URL in the feed.
