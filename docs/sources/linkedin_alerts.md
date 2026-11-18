---
name: linkedin_alerts
title: "LinkedIn job-alert emails (own mailbox)"
terms_urls:
  - https://www.linkedin.com/legal/user-agreement
checked: 2026-11-16
rate_limit: "n/a — reads your IMAP mailbox read-only"
min_interval_s: 0.0
max_requests_per_run: 0
status: approved   # approved | needs_review | blocked
verifiable: false
---

# LinkedIn job-alert emails (own mailbox)

- job-radar parses alert emails already delivered to your mailbox and never opens linkedin.com.
- User Agreement (effective 3 Nov 2025): "Services" include LinkedIn communications. §8.2 forbids scraping/copying the Services with automated means, using bots to access them, deep-linking, and copying/distributing information obtained from them without consent. There is no clause specific to alert emails.
- Therefore: personal use only, links are never followed automatically, nothing from these emails is published (aggregates exclude this source unless you decide otherwise).
- Jobs are `unverified` until a verifiable source confirms the same job.
