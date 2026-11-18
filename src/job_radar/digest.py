"""Weekly e-mail digest: new live jobs in the apply / stretch buckets since the last digest."""

from __future__ import annotations

import smtplib
from datetime import timedelta
from email.message import EmailMessage
from html import escape

from pymongo.database import Database

from .config import private_path, settings
from .match.matcher import application_note, match_all
from .match.profile import Profile
from .models import utcnow
from .pipeline.freshness import _aware
from .report.html import safe_url


def build_digest(db: Database, profile: Profile, days: int = 7) -> tuple[str, str, int]:
    last = db.digests.find_one(sort=[("sent_at", -1)])
    since = _aware(last["sent_at"]) if last else utcnow() - timedelta(days=days)
    fresh = [(j, r) for j, r in match_all(db, profile)
             if r.bucket in ("apply", "stretch") and _aware(j.get("first_seen")) and _aware(j["first_seen"]) > since]
    apply = [m for m in fresh if m[1].bucket == "apply"]
    stretch = [m for m in fresh if m[1].bucket == "stretch"]

    def block(title: str, items: list) -> str:
        if not items:
            return ""
        li = []
        for job, res in items[:25]:
            extra = ""
            if res.bucket == "stretch":
                extra = f"<br><small>Не хватает: {escape(', '.join(res.missing_required) or ', '.join(res.gaps))}. " \
                        f"{escape(application_note(res))}</small>"
            unverified = " <small>(не проверено)</small>" if job.get("status") == "unverified" else ""
            li.append(f"<li><a href='{safe_url(job.get('url'))}'>{escape(job.get('title', ''))}</a> — "
                      f"{escape(job.get('company', ''))}, {escape(job.get('city') or '')}{unverified}{extra}</li>")
        return f"<h3>{title} ({len(items)})</h3><ul>{''.join(li)}</ul>"

    html = (f"<p>Новые актуальные вакансии с {since:%d.%m.%Y}.</p>"
            + block("Подходят — можно подаваться", apply)
            + block("Доработать", stretch)
            + "<p><small>Закрытые вакансии исключены. job-radar</small></p>")
    subject = f"job-radar: {len(apply)} подходят, {len(stretch)} доработать"
    return subject, html, len(fresh)


def send_digest(db: Database, profile: Profile, dry_run: bool = False) -> str:
    subject, html, n = build_digest(db, profile)
    s = settings()
    if dry_run or not (s.smtp_host and s.digest_to):
        path = private_path("digest-preview.html")
        path.write_text(f"<h2>{escape(subject)}</h2>{html}", encoding="utf-8")
        return f"preview written to {path} ({n} jobs); SMTP not used"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.smtp_user or s.digest_to
    msg["To"] = s.digest_to
    msg.set_content("HTML digest; open in an HTML-capable mail client.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    db.digests.insert_one({"sent_at": utcnow(), "jobs": n, "subject": subject})
    return f"sent to {s.digest_to}: {subject}"
