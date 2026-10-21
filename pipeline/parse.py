"""PDF -> per-page text, keeping page numbers (1-based)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass
class Page:
    n: int
    text: str


def read_pages(path: Path) -> list[Page]:
    with pymupdf.open(path) as d:
        return [Page(n=i + 1, text=p.get_text("text")) for i, p in enumerate(d)]


def lines(pages: list[Page]) -> list[tuple[int, str]]:
    """Flattened (page, line) list with blank lines removed; the shape most parsers want."""
    out = []
    for p in pages:
        for ln in p.text.splitlines():
            ln = ln.strip()
            if ln:
                out.append((p.n, ln))
    return out
