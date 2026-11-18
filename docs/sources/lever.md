---
name: lever
title: "Lever Postings API"
terms_urls:
  - https://github.com/lever/postings-api
checked: 2026-11-16
rate_limit: "none for GET (2/s limit applies only to application POSTs)"
min_interval_s: 2.0
max_requests_per_run: 300
status: approved   # approved | needs_review | blocked
verifiable: true
---

# Lever Postings API

- Public, documented; no auth for GET. Docs note that published postings may be read by third parties and that the API is meant for building job sites.
- No terms or licence file found.
- Full descriptions, `workplaceType`, salary range. EU instance: api.eu.lever.co (`eu: true` in companies.yaml).
