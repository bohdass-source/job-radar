"""Local receiver for the "send this page to job-radar" bookmarklet.

Binds to 127.0.0.1 by default. Every request must carry INBOX_TOKEN, because any
website you visit could otherwise POST to a localhost port. Pages land in the
`inbox` collection; the `manual` source turns them into postings on the next
`collect --source manual`.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import compare_digest
from urllib.parse import parse_qs, urlsplit

from .config import settings
from .db import get_db
from .models import utcnow

MAX_BODY = 600_000

BOOKMARKLET = """javascript:(()=>{const d={url:location.href,title:document.title,
text:document.body.innerText.slice(0,200000),
jsonld:[...document.querySelectorAll('script[type="application/ld+json"]')].map(s=>s.textContent).slice(0,10)};
fetch('http://127.0.0.1:%(port)d/inbox?token=%(token)s',{method:'POST',mode:'no-cors',
headers:{'Content-Type':'text/plain'},body:JSON.stringify(d)}).then(()=>alert('job-radar: sent'))
.catch(e=>alert('job-radar: receiver not running ('+e+')'))})();"""


def bookmarklet() -> str:
    s = settings()
    if not s.inbox_token:
        raise RuntimeError("Set INBOX_TOKEN in .env first")
    return "".join(BOOKMARKLET.splitlines()) % {"port": s.inbox_port, "token": s.inbox_token}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, text: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode())

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        token = (parse_qs(parts.query).get("token") or [""])[0]
        expected = settings().inbox_token
        if parts.path != "/inbox" or not expected or not compare_digest(token, expected):
            return self._reply(403, "forbidden")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(413, "too large")
        try:
            page = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return self._reply(400, "bad json")
        url = str(page.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return self._reply(400, "bad url")
        doc = {"url": url[:2000], "title": str(page.get("title", ""))[:500], "text": str(page.get("text", ""))[:200_000],
               "jsonld": [str(x)[:100_000] for x in (page.get("jsonld") or [])][:10],
               "received_at": utcnow(), "processed": False}
        get_db().inbox.update_one({"url": doc["url"]}, {"$set": doc}, upsert=True)
        self._reply(200, "ok")

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        pass  # keep URLs of pages you browse out of logs


def serve() -> None:
    s = settings()
    if not s.inbox_token:
        raise RuntimeError("Set INBOX_TOKEN in .env first")
    server = ThreadingHTTPServer((s.inbox_bind, s.inbox_port), Handler)
    print(f"job-radar inbox listening on http://{s.inbox_bind}:{s.inbox_port}/inbox")
    server.serve_forever()
