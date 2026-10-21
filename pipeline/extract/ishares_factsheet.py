"""iShares monthly factsheet (Swiss English edition) -> facts."""
from __future__ import annotations

import re
from datetime import datetime

from ..parse import Page, lines
from ..schema import ExchangeListing, Fact, Weight, YearReturn

KV = re.compile(r"^([A-Z][A-Za-z0-9 ()&/'’.-]{1,60}?) : (.*)$")
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
SHARE_CLASS = re.compile(r"^(Hedged )?([A-Za-z. ]+?) \((Accumulating|Distributing)\)$")
KNOWN_EXCHANGES = [
    "SIX Swiss Exchange", "London Stock Exchange", "Euronext Amsterdam", "Deutsche Boerse Xetra", "Xetra",
    "Borsa Italiana", "Euronext Paris", "Bolsa Mexicana De Valores", "Euronext Brussels",
    "Nasdaq Copenhagen", "Nasdaq Stockholm", "Nasdaq Helsinki", "Oslo Stock Exchange",
    "Santiago Stock Exchange", "Bolsa De Madrid", "Vienna Stock Exchange", "Cboe Europe",
]
CCY_NAMES = {"U.S. Dollar": "USD", "Euro": "EUR", "Swiss Franc": "CHF", "Pound Sterling": "GBP",
             "British Pound": "GBP", "Japanese Yen": "JPY"}


def _is_header(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def key_values(ls: list[tuple[int, str]]) -> dict[str, tuple[str, int]]:
    """'Key : Value' pairs; a value may wrap onto up to two following lines."""
    out: dict[str, tuple[str, int]] = {}
    i = 0
    while i < len(ls):
        page, ln = ls[i]
        m = KV.match(ln)
        if not m:
            i += 1
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        j = i + 1
        while j < len(ls) and j <= i + 2:
            nxt = ls[j][1]
            if KV.match(nxt) or _is_header(nxt) or nxt.startswith("Please refer"):
                break
            val += " " + nxt
            j += 1
        out.setdefault(key, (re.sub(r"\s+", " ", val).strip(), page))
        i = j
    return out


def _f(s: str) -> float | None:
    s = s.replace(",", "").replace("%", "").strip()
    return float(s) if NUM.match(s) else None


def _iso(d: str) -> str | None:
    try:
        return datetime.strptime(d.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def calendar_returns(ls: list[tuple[int, str]]) -> tuple[list[YearReturn], int] | None:
    idx = next((i for i, (_, l) in enumerate(ls) if l == "CALENDAR YEAR PERFORMANCE"), None)
    if idx is None:
        return None
    page = ls[idx][0]
    i = idx + 1
    years = []
    while i < len(ls) and re.fullmatch(r"(19|20)\d\d", ls[i][1]):
        years.append(int(ls[i][1]))
        i += 1

    def row(label: str, start: int) -> tuple[list[float | None], int] | None:
        if start >= len(ls) or not ls[start][1].startswith(label):
            return None
        vals = [None if v == "-" else _f(v) for _, v in ls[start + 1:start + 1 + len(years)]]
        return vals, start + 1 + len(years)

    fund = row("Share Class", i)
    if not years or not fund:
        return None
    bench = row("Benchmark", fund[1])
    bvals = bench[0] if bench else [None] * len(years)
    return [YearReturn(year=y, fund=f, bench=b) for y, f, b in zip(years, fund[0], bvals)], page


def top_holdings(ls: list[tuple[int, str]]) -> tuple[list[Weight], int] | None:
    idx = next((i for i, (_, l) in enumerate(ls) if re.match(r"^(Top 10 (Holdings|Issuers)|TOP ISSUERS|TOP 10 HOLDINGS)$", l)), None)
    if idx is None:
        return None
    out, i = [], idx + 1
    while i + 1 < len(ls) and not ls[i][1].startswith("Total of Portfolio") and len(out) < 10:
        name, w = ls[i][1], ls[i + 1][1]
        if w.endswith("%") and _f(w) is not None:
            out.append(Weight(name=name, w=_f(w)))
            i += 2
        else:
            i += 1
    return (out, ls[idx][0]) if out else None


def trading_listings(ls: list[tuple[int, str]]) -> tuple[list[ExchangeListing], int] | None:
    idx = next((i for i, (_, l) in enumerate(ls) if l == "TRADING INFORMATION"), None)
    if idx is None:
        return None
    seg = [l for _, l in ls[idx + 1: idx + 80]]
    try:
        t = seg.index("Ticker")
        b = next(i for i, l in enumerate(seg) if l.startswith("Bloomberg"))
        c = next(i for i, l in enumerate(seg) if l.startswith("Listing"))
    except (ValueError, StopIteration):
        return None
    exch_text = " ".join(seg[1:t])
    found: list[tuple[int, str]] = []
    for e in sorted(KNOWN_EXCHANGES, key=len, reverse=True):
        for m in re.finditer(re.escape(e), exch_text):
            if not any(a <= m.start() < a + len(x) for a, x in found):
                found.append((m.start(), e))
    exchanges = [e for _, e in sorted(found)]
    tickers = seg[t + 1:b]
    n = len(tickers)
    first = c + 1 if seg[c] == "Listing Currency" else c + 2
    ccys = [x if re.fullmatch(r"[A-Z]{3}", x) else None for x in seg[first:first + n]]
    if len(exchanges) != n:
        exchanges = (exchanges + ["?"] * n)[:n]
    return [ExchangeListing(exchange=e, ticker=tk, ccy=cc) for e, tk, cc in zip(exchanges, tickers, ccys)], ls[idx][0]


def replication(method: str | None, structure: str | None) -> str | None:
    if structure and "synthetic" in structure.lower():
        return "synthetic"
    if not method:
        return None
    m = method.lower()
    if "replicat" in m:
        return "physical-full"
    if "optimi" in m or "sampl" in m:
        return "physical-sampling"
    return None


def parse_factsheet(pages: list[Page], doc_id: str) -> tuple[dict[str, Fact], dict]:
    """Returns (facts, meta). meta carries as_of and anything that is not a Fund field."""
    ls = lines(pages)
    kv = key_values(ls)
    out: dict[str, Fact] = {}
    meta: dict = {}

    def put(field: str, value, page: int) -> None:
        if value is not None:
            out[field] = Fact(v=value, s=doc_id, p=page)

    def g(key: str):
        return kv.get(key, (None, None))

    for line_page, ln in ls[:20]:
        if m := re.search(r"as at:\s*(\d{2}-[A-Za-z]{3}-\d{4})", ln):
            meta["as_of"] = _iso(m.group(1))
        if m := SHARE_CLASS.match(ln):
            put("hedged", bool(m.group(1)), line_page)
            if m.group(1):
                put("hedge_currency", CCY_NAMES.get(m.group(2).strip(), m.group(2).strip()), line_page)

    v, p = g("Total Expense Ratio")
    put("ter", _f(v) if v else None, p)
    v, p = g("Share Class Currency")
    put("share_class_currency", v, p)
    v, p = g("Use of Income")
    put("use_of_income", v, p)
    v, p = g("Benchmark")
    if not v:  # active funds name a constraint or comparator index instead of a benchmark
        for key in ("Constraint 1", "Comparator 1", "Comparator 2"):
            v, p = g(key)
            if v:
                v = f"{v} ({key.split()[0].lower()})"
                break
    put("benchmark", v, p)
    v, p = g("Domicile")
    put("domicile", v, p)
    v, p = g("Share Class Launch Date")
    put("inception", _iso(v) if v else None, p)
    v, p = g("SFDR Classification")
    put("sfdr", v, p)
    v, p = g("Number of Holdings")
    put("holdings_count", int(_f(v)) if v and _f(v) is not None else None, p)
    v, p = g("Net Assets of Fund (M)")
    if v and (m := re.match(r"([\d,]+(?:\.\d+)?)\s+([A-Z]{3})", v)):
        put("fund_size_m", float(m.group(1).replace(",", "")), p)
        meta["fund_size_ccy"] = m.group(2)
    mv, mp = g("Methodology")
    sv, _ = g("Product Structure")
    put("replication", replication(mv, sv), mp)

    if r := calendar_returns(ls):
        put("calendar_returns", r[0], r[1])
    if r := top_holdings(ls):
        put("top_holdings", r[0], r[1])
    if r := trading_listings(ls):
        put("listings", r[0], r[1])
    return out, meta
