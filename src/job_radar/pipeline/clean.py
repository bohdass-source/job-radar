"""HTML to text, whitespace, and a small stop-word language detector (de / en)."""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    if "<" not in raw:
        return normalize_ws(html.unescape(raw))
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return normalize_ws("".join(parser.parts))


def normalize_ws(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


_DE = set("""und der die das ist nicht mit für auf wir sie ein eine du ihr uns bei zu von im den dem des oder als
auch werden wird sind hast hat deine dein unsere unser über kenntnisse erfahrung aufgaben bewerbung
wir bieten sowie gute sehr""".split())
_EN = set("""and the is are with for on we you our your a an to of in as or be will this that have has experience
skills team role work about what who years strong ability""".split())


def detect_language(text: str) -> str:
    """'de', 'en', 'mixed' or 'unknown'. Stop-word counting is enough for job ads,
    which are long and almost always written in one of the two languages."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if len(words) < 8:
        return "unknown"
    de = sum(w in _DE for w in words)
    en = sum(w in _EN for w in words)
    if de + en < 4:
        return "unknown"
    share = de / (de + en)
    if share >= 0.7:
        return "de"
    if share <= 0.3:
        return "en"
    return "mixed"
