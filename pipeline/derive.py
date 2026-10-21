"""Derived numbers: tracking difference and theme tags."""
from __future__ import annotations

import re

from .schema import Fund


def tracking_difference(f: Fund) -> None:
    """Average (fund - benchmark) over the last 3 and 5 complete calendar years with both values.

    Negative means the fund lagged its index (the usual case: costs). Only full windows count,
    so a fund with 4 years of history gets a 3y value but no 5y value.
    """
    if not f.calendar_returns or (f.replication and f.replication.v == "active"):
        return  # an active fund's comparator is not an index it tries to track
    diffs = [(r.year, r.fund - r.bench) for r in f.calendar_returns.v
             if r.fund is not None and r.bench is not None]
    diffs.sort()
    for n, attr in ((3, "td_avg_3y"), (5, "td_avg_5y")):
        last = diffs[-n:]
        if len(last) == n and last[-1][0] - last[0][0] == n - 1:
            setattr(f, attr, round(sum(d for _, d in last) / n, 3))


# (tag, regex over name + benchmark). Keep tags coarse; the vector index handles nuance.
TAG_RULES: list[tuple[str, str]] = [
    ("world", r"\bworld\b|\bacwi\b|all[- ]world|all country|global(?! aggregate| semis)"),
    ("all-world", r"\bacwi\b|all[- ]world|all country|ftse global all cap"),
    ("usa", r"s&p 500|\busa?\b|u\.s\.|nasdaq|russell|msci usa|america"),
    ("europe", r"europe|stoxx|euro area|eurozone|emu\b"),
    ("switzerland", r"\bspi\b|\bsmi\b|switzerland|swiss|\(ch\)"),
    ("japan", r"japan|topix|nikkei"),
    ("uk", r"\buk\b|ftse 100|ftse 250|united kingdom"),
    ("emerging-markets", r"emerging|\bem\b|\bem imi\b"),
    ("china", r"china"),
    ("india", r"india"),
    ("asia-pacific", r"pacific|asia"),
    ("small-cap", r"small cap|smallcap|small-cap"),
    ("value", r"\bvalue\b"),
    ("momentum", r"momentum"),
    ("quality", r"\bquality\b"),
    ("min-volatility", r"min(imum)? vol"),
    ("dividend", r"dividend|high yield equity|income"),
    ("multifactor", r"multifactor|multi-factor"),
    ("esg", r"\besg\b|\bsri\b|screened|sustainab|paris[- ]aligned|climate|\bpab\b|\bctb\b"),
    ("semiconductors", r"semiconductor|\bsemis?\b"),
    ("technology", r"tech|information technology|nasdaq 100|digital|software|cloud|cyber"),
    ("artificial-intelligence", r"\bai\b|artificial intelligence|automation|robotics"),
    ("healthcare", r"health ?care|biotech|medical|pharma"),
    ("clean-energy", r"clean energy|renewable|solar|wind|hydrogen|energy transition"),
    ("energy", r"\benergy\b|oil|gas"),
    ("financials", r"financials|banks"),
    ("real-estate", r"real estate|property|reit"),
    ("infrastructure", r"infrastructure"),
    ("water", r"\bwater\b"),
    ("defense", r"defen[cs]e|aerospace"),
    ("gold", r"\bgold\b"),
    ("commodities", r"commodit"),
    ("government-bonds", r"treasury|government|govt|gilt|bund|sovereign"),
    ("corporate-bonds", r"corporate|\bcorp\b|credit"),
    ("high-yield", r"high yield|\bhy\b"),
    ("inflation-linked", r"inflation|tips\b|linker"),
    ("aggregate-bonds", r"aggregate|\bagg\b"),
    ("short-duration", r"0-1|0-3|1-3|ultrashort|short duration|short-term|floating"),
    ("money-market", r"money market|overnight|\bsofr\b|\bestr\b|\bsaron\b"),
]


def theme_tags(f: Fund) -> list[str]:
    text = " ".join(x for x in (f.name.v, f.benchmark.v if f.benchmark else "") if x).lower()
    tags = [t for t, rx in TAG_RULES if re.search(rx, text)]
    if f.asset_class:
        ac = f.asset_class.v.lower()
        tags.append({"equity": "equity", "fixed income": "bonds", "commodity": "commodities",
                     "real estate": "real-estate"}.get(ac, ac.replace(" ", "-")))
    if f.replication and f.replication.v == "active":
        tags.append("active")
    if f.hedged and f.hedged.v:
        tags.append("currency-hedged")
        if f.hedge_currency and f.hedge_currency.v == "CHF":
            tags.append("chf-hedged")
    if f.sectors and f.sectors.v and f.sectors.v[0].w >= 60:
        top = f.sectors.v[0].name.lower()
        if "information technology" in top:
            tags.append("technology")
    return sorted(set(tags))
