"""Vanguard factsheet (Swiss English edition) -> facts.

The key facts table is laid out as a block of labels followed by a block of values; the calendar
year table uses "01 Jan YYYY - 31 Dec YYYY" period lines followed by fund and benchmark rows.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..parse import Page, lines
from ..schema import Fact, Weight, YearReturn

LABELS = ["Base currency", "Tax reporting", "SRI‡", "Index ticker", "Investment structure", "Domicile",
          "Legal entity", "Investment method", "ISIN", "Dividends", "Dividend schedule", "Investment manager",
          "Benchmark", "Share class currency"]
CCY_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "CHF": "CHF"}
NUM = re.compile(r"^-?\d+(?:\.\d+)?%?$")


def _f(s: str) -> float | None:
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def label_blocks(ls: list[tuple[int, str]]) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    i = 0
    while i < len(ls):
        j = i
        while j < len(ls) and ls[j][1] in LABELS:
            j += 1
        k = j - i
        if k >= 2:
            for n in range(k):
                if j + n < len(ls):
                    out.setdefault(ls[i + n][1].rstrip("‡"), (ls[j + n][1], ls[j + n][0]))
            i = j + k
        else:
            i += 1
    return out


def calendar_returns(ls: list[tuple[int, str]]) -> tuple[list[YearReturn], int] | None:
    idx = next((i for i, (_, l) in enumerate(ls) if l.startswith("Performance summary")), None)
    if idx is None:
        return None
    years, i = [], idx + 1
    while i < len(ls) and not ls[i][1].startswith("Fund"):
        if m := re.fullmatch(r"31 Dec (\d{4})", ls[i][1]):
            years.append(int(m.group(1)))
        i += 1
    if not years or i >= len(ls):
        return None

    def row(start: int) -> tuple[list[float | None], int]:
        # Columns before inception are "—" placeholders, so read the whole row and keep the last N.
        vals, j = [], start + 1
        while j < len(ls) and (ls[j][1] in ("—", "-", "–") or NUM.match(ls[j][1])):
            v = ls[j][1]
            vals.append(None if v in ("—", "-", "–") else _f(v))
            j += 1
        vals = vals[-len(years):]
        return [None] * (len(years) - len(vals)) + vals, j

    fund, j = row(i)
    bench = [None] * len(years)
    if j < len(ls) and ls[j][1].startswith("Benchmark"):
        bench, _ = row(j)
    return [YearReturn(year=y, fund=a, bench=b) for y, a, b in zip(years, fund, bench)], ls[idx][0]


def weights_after(ls: list[tuple[int, str]], marker: str, limit: int = 15) -> tuple[list[Weight], int] | None:
    """Name / value pairs after a marker line; the marker line may carry the first name glued on."""
    idx = next((i for i, (_, l) in enumerate(ls) if l.startswith(marker)), None)
    if idx is None:
        return None
    out, i = [], idx + 1
    while i + 1 < len(ls) and len(out) < limit:
        name, val = ls[i][1].strip(), ls[i + 1][1].strip()
        if re.search(r"\d", name) or sum(w.w for w in out) + (_f(val) or 0) > 100.5:
            break
        if NUM.match(val) and not NUM.match(name):
            out.append(Weight(name=name, w=_f(val)))
            i += 2
        elif out:
            break
        else:
            i += 1
    return (out, ls[idx][0]) if out else None


def parse_factsheet(pages: list[Page], doc_id: str) -> tuple[dict[str, Fact], dict]:
    ls = lines(pages)
    text = "\n".join(l for _, l in ls)
    out: dict[str, Fact] = {}
    meta: dict = {}

    def put(field: str, value, page: int | None) -> None:
        if value is not None:
            out[field] = Fact(v=value, s=doc_id, p=page)

    def page_of(pattern: str) -> int | None:
        return next((p for p, l in ls if re.search(pattern, l)), None)

    if m := re.search(r"Factsheet \| (\d{1,2} \w+ \d{4})", text):
        meta["as_of"] = datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()
    if m := re.search(r"Inception date: (\d{1,2} \w+ \d{4})", text):
        put("inception", datetime.strptime(m.group(1), "%d %B %Y").date().isoformat(), page_of("Inception date"))
    if m := re.search(r"Total assets \(million\) ([$€£]|CHF ?)([\d,]+(?:\.\d+)?)", text):
        put("fund_size_m", float(m.group(2).replace(",", "")), page_of("Total assets"))
        meta["fund_size_ccy"] = CCY_SYMBOL.get(m.group(1).strip(), m.group(1).strip())
    if m := re.search(r"Ongoing Charges Figure†?\s*(\d+(?:\.\d+)?)%", text):
        put("ter", float(m.group(1)), page_of("Ongoing Charges Figure"))
    if m := re.search(r"Benchmark\s+[—-]\s+(.+?)(?:\s+-\s+The\b.*)?$", text, re.M):
        put("benchmark", m.group(1).strip(), page_of(r"Benchmark\s+[—-]"))
    if m := re.search(r"^\(([A-Z]{3})\)\s+(Hedged\s+)?(Accumulating|Distributing)", text, re.M):
        put("share_class_currency", m.group(1), 1)
        put("use_of_income", m.group(3), 1)

    kv = label_blocks(ls)
    if "Domicile" in kv:
        put("domicile", kv["Domicile"][0], kv["Domicile"][1])
    if "SRI" in kv and kv["SRI"][0].isdigit():
        put("sri", int(kv["SRI"][0]), kv["SRI"][1])
    method = kv.get("Investment method", (None, None))
    objective = text.lower()
    if method[0]:
        m = method[0].lower()
        rep = "synthetic" if "synthetic" in m else (
            "physical-sampling" if ("sampl" in m or "representative sample" in objective) else "physical-full")
        put("replication", rep, method[1])

    if m := re.search(r"Number of (stocks|bonds)\s+([\d,]+)", text):
        put("holdings_count", int(m.group(2).replace(",", "")), page_of(r"Number of (stocks|bonds)"))

    if r := calendar_returns(ls):
        put("calendar_returns", r[0], r[1])
    if r := weights_after(ls, "Top 10 holdings", 10):
        put("top_holdings", r[0], r[1])
    for marker, field in (("Weighted exposure", "sectors"), ("Market allocation", "countries")):
        if r := weights_after(ls, marker):
            ws = r[0]
            # the marker line repeats the first name ("Weighted exposureTechnology"); drop duplicates
            seen, uniq = set(), []
            for w in ws:
                if w.name not in seen:
                    seen.add(w.name)
                    uniq.append(w)
            rest = round(100 - sum(w.w for w in uniq), 2)
            if 0.5 < rest < 60:
                uniq.append(Weight(name="Other", w=rest))
            put(field, uniq, r[1])
    return out, meta
