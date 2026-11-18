---
name: jooble
title: "Jooble REST API"
terms_urls:
  - https://jooble.org/api/about
  - https://jooble.org/info/terms
checked: 2026-11-16
rate_limit: "not published"
min_interval_s: 3.0
max_requests_per_run: 100
status: approved   # approved | needs_review | blocked
verifiable: true
---

# Jooble REST API

- Key via request form on jooble.org/api/about; the API is aimed at webmasters showing Jooble results on their site.
- Site terms (effective 8 Jan 2019) §4(m) forbid bots/crawlers on the site and §4(i) forbid copying content for republication without written permission. They do not mention the API.
- Endpoint and fields in the adapter come from Jooble's code samples; confirm them with the docs sent with the key.
- Open question for Jooble (compliance@jooble.com): is storing API results locally for a personal job search covered by the key?
- Descriptions are snippets.
