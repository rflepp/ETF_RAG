"""PRIIPs KID parser. The KID layout is prescribed by EU regulation, so this works across providers."""
from __future__ import annotations

import re

from ..parse import Page
from ..schema import Fact

PCT = r"(-?\d+(?:[.,]\d+)?)\s?%"


def _find(pages: list[Page], pattern: str, flags=re.I | re.S) -> tuple[re.Match, int] | None:
    for p in pages:
        m = re.search(pattern, p.text, flags)
        if m:
            return m, p.n
    return None


def _pct(s: str) -> float:
    return float(s.replace(",", "."))


def parse_kid(pages: list[Page], doc_id: str) -> dict[str, Fact]:
    out: dict[str, Fact] = {}

    def put(field: str, value, page: int) -> None:
        out[field] = Fact(v=value, s=doc_id, p=page)

    if r := _find(pages, r"classified\s+this\s+(?:product|fund)\s+as\s+(\d)\s+out\s+of\s+7"):
        put("sri", int(r[0].group(1)), r[1])

    if r := _find(pages, r"Recommended Holding Period:?\s*(\d+)\s*years?"):
        put("rhp_years", int(r[0].group(1)), r[1])

    # Ongoing costs: "Management fees and other administrative or operating costs  0.20% of the value ..."
    if r := _find(pages, r"Management\s+fees\s+and\s+other\s+administrative\s+or\s+operating\s+costs\s+" + PCT):
        put("kid_ongoing_pct", _pct(r[0].group(1)), r[1])

    # Transaction costs in the cost composition table (not the "Costs over time" prose).
    if r := _find(pages, r"Transaction\s+costs\s+" + PCT + r"\s+of\s+the\s+value"):
        put("kid_transaction_pct", _pct(r[0].group(1)), r[1])

    for kind, field in (("entry", "kid_entry_pct"), ("exit", "kid_exit_pct")):
        if r := _find(pages, rf"(?:We|The\s+Fund)\s+do(?:es)?\s+not\s+charge\s+an?\s+{kind}\s+fee"):
            put(field, 0.0, r[1])
        elif r := _find(pages, rf"{kind.capitalize()} costs\s+(?:[^%\n]*\n){{0,3}}?[^%\n]*?" + PCT):
            put(field, _pct(r[0].group(1)), r[1])

    # Entry/exit fees are usually primary-market maxima; exchange buyers do not pay them.
    if r := _find(pages, r"Not applicable to secondary market investors"):
        put("kid_one_off_secondary_exempt", True, r[1])

    return out
