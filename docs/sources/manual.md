---
name: manual
title: "Manual: send the page open in your browser"
terms_urls: []
checked: 2026-11-16
rate_limit: "n/a — one request per click, made by you"
min_interval_s: 0.0
max_requests_per_run: 0
status: approved   # approved | needs_review | blocked
verifiable: false
---

# Manual: send the page open in your browser

- A bookmarklet sends the text and schema.org JobPosting data of the page you are looking at to the local receiver (127.0.0.1, protected by INBOX_TOKEN).
- No site is visited by job-radar. The site's own terms still apply to what you do with its content; job-radar keeps it private.
- `validThrough` from JSON-LD, when present, drives expiry; otherwise the job expires after the unverified TTL.
