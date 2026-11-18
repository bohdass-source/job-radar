"""LinkedIn and Indeed job-alert emails from your own mailbox (read-only IMAP).

The parser only reads the email body that is already in your mailbox. It never
opens the links: both services' terms forbid automated access to their sites, so
the stored link is for you to click. Because nothing can be re-checked
automatically, these jobs are `unverified` unless the same job also arrives from
a verifiable source (an ATS feed or an API), in which case the merge makes it
verifiable.

Alert layouts change without notice. The parser is heuristic: it finds links
that point at a job, takes the link text as the title and the next two text
lines as company and location. Run `job-radar collect --source linkedin_alerts
--dry-run` after a layout change to see what it extracts.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import date, timedelta
from email import policy
from html.parser import HTMLParser
from typing import Iterator

from ..config import settings
from ..models import Posting
from .base import SourceAdapter

_SKIP_TEXT = re.compile(r"^(view job|apply|jetzt bewerben|easy apply|see all jobs|alle jobs|unsubscribe|abmelden"
                        r"|job ansehen|stelle ansehen|\d+ (applicants|bewerber).*|new|neu|promoted|gesponsert)$", re.I)


class _Flatten(HTMLParser):
    """Turn an email body into a flat list of ("a", href, text) and ("t", text) items."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, ...]] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._flush()
            self._href = dict(attrs).get("href") or ""
        elif tag in ("br", "p", "div", "td", "tr", "li", "span", "table"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            text = " ".join("".join(self._buf).split())
            self.items.append(("a", self._href or "", text))
            self._href, self._buf = None, []
        elif tag in ("p", "div", "td", "tr", "li", "span", "table"):
            self._flush()

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def _flush(self) -> None:
        if self._href is not None:
            return
        text = " ".join("".join(self._buf).split())
        if text:
            self.items.append(("t", text))
        self._buf = []


def parse_alert_html(body: str, job_pattern: re.Pattern[str], canonical: str) -> list[dict[str, str]]:
    """Generic alert parser shared by LinkedIn and Indeed."""
    flat = _Flatten()
    flat.feed(body)
    flat.close()
    jobs: dict[str, dict[str, str]] = {}
    order: list[str] = []
    current: str | None = None
    trailing: dict[str, list[str]] = {}
    for item in flat.items:
        if item[0] == "a":
            _, href, text = item
            m = job_pattern.search(href)
            if m:
                jid = m.group(1)
                if jid not in jobs:
                    jobs[jid] = {"id": jid, "title": "", "url": canonical.format(id=jid)}
                    order.append(jid)
                    trailing[jid] = []
                if text and not _SKIP_TEXT.match(text) and len(text) > len(jobs[jid]["title"]):
                    jobs[jid]["title"] = text
                current = jid
                continue
            text = item[2]
        else:
            text = item[1]
        if current and text and not _SKIP_TEXT.match(text) and len(trailing[current]) < 3:
            if text != jobs[current]["title"]:
                trailing[current].append(text)
    out = []
    for jid in order:
        job = jobs[jid]
        lines = trailing[jid]
        company, location = "", ""
        if lines and "·" in lines[0]:
            company, _, location = (s.strip() for s in lines[0].partition("·"))
        elif lines:
            company = lines[0]
            location = lines[1] if len(lines) > 1 else ""
        if job["title"]:
            out.append({**job, "company": company, "location": location})
    return out


class _MailAlertAdapter(SourceAdapter):
    verifiable = False
    SENDERS: tuple[str, ...] = ()
    JOB_PATTERN: re.Pattern[str]
    CANONICAL = ""

    def fetch(self) -> Iterator[Posting]:
        for msg_id, body in self._messages():
            for job in parse_alert_html(body, self.JOB_PATTERN, self.CANONICAL):
                city = job["location"].split(",")[0].strip()
                yield Posting(
                    source=self.name, source_id=job["id"], title=job["title"], company=job["company"],
                    location=job["location"], city=city,
                    remote=True if re.search(r"remote|homeoffice|home office", job["location"], re.I) else None,
                    url=job["url"], description="", description_is_snippet=True,
                    extra={"email_id": msg_id},
                )

    def _messages(self) -> Iterator[tuple[str, str]]:
        s = settings()
        if not (s.imap_host and s.imap_user and s.imap_password):
            raise RuntimeError("IMAP_HOST / IMAP_USER / IMAP_PASSWORD are not set in .env")
        since = (date.today() - timedelta(days=int(self.cfg.get("days_back", 14)))).strftime("%d-%b-%Y")
        senders = self.cfg.get("senders") or self.SENDERS
        with imaplib.IMAP4_SSL(s.imap_host) as imap:
            imap.login(s.imap_user, s.imap_password)
            imap.select(s.imap_folder, readonly=True)   # never marks mail as read
            for sender in senders:
                typ, data = imap.search(None, "FROM", f'"{sender}"', "SINCE", since)
                if typ != "OK":
                    continue
                for num in data[0].split():
                    typ, parts = imap.fetch(num, "(BODY.PEEK[])")
                    if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                        continue
                    msg = email.message_from_bytes(parts[0][1], policy=policy.default)
                    part = msg.get_body(preferencelist=("html", "plain"))
                    if part is not None:
                        yield str(msg.get("Message-ID", num.decode())), part.get_content()


class LinkedInAlertsAdapter(_MailAlertAdapter):
    name = "linkedin_alerts"
    SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com")
    JOB_PATTERN = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")
    CANONICAL = "https://www.linkedin.com/jobs/view/{id}/"


class IndeedAlertsAdapter(_MailAlertAdapter):
    name = "indeed_alerts"
    SENDERS = ("alert@indeed.com", "donotreply@jobalert.indeed.com", "jobalert@indeed.com")
    JOB_PATTERN = re.compile(r"indeed\.[a-z.]+/.*?[?&]jk=([0-9a-f]{8,})")
    CANONICAL = "https://de.indeed.com/viewjob?jk={id}"
