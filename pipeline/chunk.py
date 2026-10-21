"""Section-aware chunking with page numbers.

Text blocks are split at section headers (ALL CAPS lines in factsheets, the prescribed questions in
KIDs), merged up to ~TARGET words and split at sentence boundaries above MAX words. Number-only
tables (the factsheet's vertical layout) are dropped as raw text; the extracted facts are rendered
into readable "data" chunks instead, cited to the same page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import Page
from .schema import Fund

TARGET, MIN, MAX = 220, 60, 380
KID_HEADERS = re.compile(
    r"^(Purpose|Product|What is this product\?|What are the risks and what could I get in return\?|"
    r"Performance Scenarios|What happens if .{0,60}\?|What are the costs\?|Costs over time|"
    r"Composition of Costs|How long should I hold it.*|How can I complain\?|Other relevant information)",
    re.I)
SKIP_SECTIONS = re.compile(r"^(GLOSSARY|How can I complain\?)", re.I)
STOP_DOC = re.compile(r"^IMPORTANT INFORMATION|^Important information$")  # legal boilerplate runs to the end of the factsheet
NUMERIC = re.compile(r"^[-+]?[\d.,’'%]+(\s*(USD|EUR|CHF|GBP|x|yrs))?$")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_type: str
    page: int
    text: str


def _is_header(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    caps = len(letters) >= 6 and all(c.isupper() for c in letters) and len(line) < 70
    return caps or bool(KID_HEADERS.match(line))


def _running_header(pages: list[Page]) -> set[str]:
    """Lines repeated at the top of several pages (fund name, share class, umbrella)."""
    if len(pages) < 2:
        return set()
    tops = [[l.strip() for l in p.text.splitlines() if l.strip()][:4] for p in pages]
    counts: dict[str, int] = {}
    for t in tops:
        for l in set(t):
            counts[l] = counts.get(l, 0) + 1
    return {l for l, c in counts.items() if c >= 2 and len(l) < 90}


def _blocks(page: Page, skip: set[str]) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for raw in page.text.splitlines():
        line = raw.strip()
        if not line or line in skip:
            continue
        if _is_header(line) and blocks[-1]:
            blocks.append([])
        blocks[-1].append(line)
    return [b for b in blocks if b]


def _prose(block: list[str]) -> str | None:
    if SKIP_SECTIONS.match(block[0]):
        return None
    numeric = sum(1 for l in block if NUMERIC.match(l))
    if len(block) > 6 and numeric / len(block) > 0.4:
        return None
    text = " ".join(block)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text.split()) >= 8 else None


def _split_long(text: str) -> list[str]:
    words = text.split()
    if len(words) <= MAX:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], []
    for s in sentences:
        if cur and len(" ".join(cur + [s]).split()) > TARGET:
            out.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_doc(pages: list[Page], doc_id: str, doc_type: str, title: str) -> list[Chunk]:
    """title (fund name) is prefixed to every chunk so that a passage is self-describing."""
    pieces: list[tuple[int, str]] = []
    header = _running_header(pages)
    stopped = False
    for page in pages:
        if stopped:
            break
        buf = ""
        for block in _blocks(page, header):
            if STOP_DOC.match(block[0]):
                stopped = True
                break
            t = _prose(block)
            if not t:
                continue
            if buf and len((buf + " " + t).split()) > TARGET:
                pieces.append((page.n, buf))
                buf = t
            else:
                buf = (buf + " " + t).strip()
        if buf:
            if pieces and pieces[-1][0] == page.n and len(buf.split()) < MIN:
                pieces[-1] = (page.n, pieces[-1][1] + " " + buf)
            else:
                pieces.append((page.n, buf))
    out = []
    for n, (page_no, text) in enumerate((p, s) for p, t in pieces for s in _split_long(t)):
        out.append(Chunk(chunk_id=f"{doc_id}#{n}", doc_id=doc_id, doc_type=doc_type, page=page_no,
                         text=f"[{title} | {doc_type}] {text}"))
    return out


def data_chunks(f: Fund) -> list[Chunk]:
    """Readable renderings of extracted tables, so retrieval can find them by meaning."""
    out: list[Chunk] = []
    title = f.name.v

    def add(src: str, page: int | None, text: str) -> None:
        doc = next((d for d in f.docs if d.doc_id == src), None)
        out.append(Chunk(chunk_id=f"{src}#d{len(out)}", doc_id=src, doc_type=doc.doc_type if doc else "data",
                         page=page or 1, text=f"[{title} | data] {text}"))

    if f.calendar_returns:
        rows = "; ".join(f"{r.year}: fund {_fmt(r.fund)}%, benchmark {_fmt(r.bench)}%"
                         for r in f.calendar_returns.v if r.fund is not None)
        td = ""
        if f.td_avg_3y is not None:
            td += f" Average tracking difference (fund minus benchmark) over 3 years: {f.td_avg_3y:+.2f} pp per year."
        if f.td_avg_5y is not None:
            td += f" Over 5 years: {f.td_avg_5y:+.2f} pp per year."
        add(f.calendar_returns.s, f.calendar_returns.p, f"Calendar year performance in "
            f"{f.share_class_currency.v if f.share_class_currency else 'share class currency'}: {rows}.{td}")
    if f.listings:
        ls = "; ".join(f"{l.exchange}: ticker {l.ticker}, trading currency {l.ccy}" for l in f.listings.v)
        add(f.listings.s, f.listings.p, f"Exchange listings: {ls}.")
    if f.top_holdings:
        hs = ", ".join(f"{h.name} {h.w:.2f}%" for h in f.top_holdings.v)
        add(f.top_holdings.s, f.top_holdings.p, f"Top holdings: {hs}.")
    for name in ("sectors", "countries"):
        fact = getattr(f, name)
        if fact:
            ws = ", ".join(f"{w.name} {w.w:.1f}%" for w in fact.v)
            add(fact.s, fact.p, f"{name.capitalize()} breakdown: {ws}.")
    return out


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"
