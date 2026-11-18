"""Benchmarks. Results go into README between BENCH markers."""

from __future__ import annotations

import re

from ..config import ROOT


def write_readme_block(name: str, markdown: str) -> None:
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    start, end = f"<!-- BENCH:{name}:START -->", f"<!-- BENCH:{name}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise ValueError(f"README has no {start} ... {end} block")
    path.write_text(pattern.sub(lambda _: f"{start}\n{markdown}\n{end}", text), encoding="utf-8")
