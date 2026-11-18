"""Pages you send yourself from the browser via the bookmarklet (see `job-radar inbox`).

The bookmarklet runs in *your* browser on a page *you* opened and posts the page
text plus any schema.org JobPosting JSON-LD to the local receiver. Nothing here
visits a site. If the page carries `validThrough`, that date drives freshness;
otherwise the job is `unverified` and expires after the configured TTL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterator

from ..db import get_db
from ..models import Posting
from .base import SourceAdapter


def _find_jobposting(blocks: list[str]) -> dict[str, Any] | None:
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    return node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return None


def _date(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def posting_from_page(page: dict[str, Any]) -> Posting:
    url = page.get("url", "")
    jp = _find_jobposting(page.get("jsonld") or []) or {}
    org = jp.get("hiringOrganization") or {}
    loc = jp.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    salary = (jp.get("baseSalary") or {}).get("value") or {}
    remote = True if jp.get("jobLocationType") == "TELECOMMUTE" else None
    return Posting(
        source="manual",
        source_id=hashlib.sha1(url.encode()).hexdigest()[:16],
        title=jp.get("title") or page.get("title", ""),
        company=org.get("name", "") if isinstance(org, dict) else str(org),
        location=", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressCountry")
                                         if isinstance(addr.get("addressCountry"), str) else None])),
        city=addr.get("addressLocality", "") or "",
        remote=remote,
        url=url,
        description=jp.get("description") or page.get("text", ""),
        posted_at=_date(jp.get("datePosted")),
        salary_min=salary.get("minValue") if isinstance(salary, dict) else None,
        salary_max=salary.get("maxValue") if isinstance(salary, dict) else None,
        salary_currency=(jp.get("baseSalary") or {}).get("currency"),
        salary_period={"YEAR": "year", "MONTH": "month", "HOUR": "hour"}.get(
            (salary.get("unitText") or "").upper() if isinstance(salary, dict) else ""),
        extra={"valid_through": jp.get("validThrough"), "has_jsonld": bool(jp)},
    )


class ManualAdapter(SourceAdapter):
    name = "manual"
    verifiable = False

    def fetch(self) -> Iterator[Posting]:
        db = get_db()
        for page in db.inbox.find({"processed": False}):
            yield posting_from_page(page)
            db.inbox.update_one({"_id": page["_id"]}, {"$set": {"processed": True}})
