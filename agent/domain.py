"""Swiss trading costs and the profile shortlist. Python port of site/js/costs.js and site/js/rank.js;
tests/test_agent_domain.py checks both implementations give the same numbers on shared cases."""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "site" / "rules" / "ch_trading.json"

NARROW = {"semiconductors", "technology", "artificial-intelligence", "healthcare", "clean-energy", "energy", "financials",
          "real-estate", "infrastructure", "water", "defense", "gold", "commodities", "small-cap", "value", "momentum",
          "quality", "min-volatility", "dividend", "multifactor", "china", "india"}
THEMES = {"semiconductors": ["semiconductors"], "technology": ["technology"], "ai": ["artificial-intelligence"],
          "healthcare": ["healthcare"], "clean_energy": ["clean-energy"], "small_caps": ["small-cap"], "value": ["value"],
          "dividend": ["dividend"], "real_estate": ["real-estate"], "gold": ["gold"], "emerging": ["emerging-markets"]}


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES_PATH.read_text())


def broker(key: str | None) -> dict:
    b = rules()["brokers"]
    return b.get(key or "", b["other_ch"])


def _v(f: dict, k: str):
    x = f.get(k)
    return x.get("v") if isinstance(x, dict) else None


def six_line(f: dict) -> dict | None:
    ls = [l for l in (_v(f, "listings") or []) if l["exchange"] == "SIX Swiss Exchange"]
    return next((l for l in ls if l.get("ccy") == "CHF"), ls[0] if ls else None)


def is_swiss(f: dict) -> bool:
    return f["isin"].startswith("CH") or (_v(f, "domicile") or "").lower() == "switzerland"


def trade_costs(f: dict, b: dict) -> dict:
    sd = rules()["stamp_duty"]
    stamp = (sd["swiss_pct"] if is_swiss(f) else sd["foreign_pct"]) if b["swiss_dealer"] else 0.0
    line = six_line(f)
    needs_fx = not (line and line.get("ccy") == "CHF")
    return {"stamp_pct": stamp, "fx_pct": b["fx_pct"] if needs_fx else 0.0,
            "fx_ccy": (line or {}).get("ccy") or _v(f, "share_class_currency") if needs_fx else None,
            "commission_pct": b.get("commission_pct"), "six": line}


def first_year_pct(f: dict, tc: dict) -> float | None:
    ter = _v(f, "ter")
    if ter is None:
        return None
    return ter + tc["stamp_pct"] + (tc["fx_pct"] or 0) + (tc["commission_pct"] or 0)


def effective_annual_cost(f: dict) -> tuple[float, str]:
    if f.get("td_avg_5y") is not None:
        return -f["td_avg_5y"], "td5"
    if f.get("td_avg_3y") is not None:
        return -f["td_avg_3y"], "td3"
    if _v(f, "ter") is not None:
        return _v(f, "ter") + 0.05, "ter"
    return 1.0, "none"


def _has(f: dict, *tags: str) -> bool:
    return bool(set(f.get("theme_tags") or []) & set(tags))


def _ac(f: dict) -> str:
    return (_v(f, "asset_class") or "").lower()


def _passes_common(f: dict, p: dict) -> bool:
    if p.get("esg") == "only" and not _has(f, "esg"):
        return False
    inc = _v(f, "use_of_income")
    if p.get("income") == "acc" and inc and inc != "Accumulating":
        return False
    if p.get("income") == "dist" and inc and inc != "Distributing":
        return False
    if p.get("maxTer") and _v(f, "ter") is not None and _v(f, "ter") > p["maxTer"]:
        return False
    return True


def _core(f: dict, p: dict) -> bool:
    if _ac(f) != "equity" or _has(f, "active") or not _has(f, "world", "all-world") or _has(f, *NARROW) or _has(f, "emerging-markets"):
        return False
    if p.get("em") == "no" and _has(f, "all-world"):
        return False
    if p.get("em") == "yes" and not _has(f, "all-world"):
        return False
    return _has(f, "chf-hedged") if p.get("hedge") == "all" else not _has(f, "currency-hedged")


# Preferences that may be given up, one at a time, when no fund meets them all (same as RELAX in site/js/rank.js).
RELAX = [
    (lambda p: p.get("em") in ("yes", "no"), {"em": "any"},
     lambda p: "no emerging markets" if p.get("em") == "yes" else "includes emerging markets"),
    (lambda p: p.get("hedge") == "all", {"hedge": "none"}, lambda p: "not hedged to CHF"),
    (lambda p: p.get("income") in ("acc", "dist"), {"income": "any"},
     lambda p: "distributing" if p.get("income") == "acc" else "accumulating"),
    (lambda p: p.get("esg") == "only", {"esg": "any"}, lambda p: "no ESG screen"),
]


def score(f: dict, p: dict, b: dict) -> tuple[float, list[str]]:
    eff, _ = effective_annual_cost(f)
    horizon = {"long": 10, "medium": 7, "short": 3}.get(p.get("horizon"), 10)
    tc = trade_costs(f, b)
    s = eff + (tc["stamp_pct"] + (tc["fx_pct"] if tc["fx_pct"] is not None else 0.5) + (tc["commission_pct"] or 0)) / horizon
    reasons = []
    size = _v(f, "fund_size_m")
    if size is not None and size < 100:
        s += 0.15
        reasons.append("small fund")
    elif size is not None and size < 500:
        s += 0.03
    inc = _v(f, "inception")
    if inc and (date.today() - date.fromisoformat(inc)).days / 365.25 < 3:
        s += 0.03
        reasons.append("young fund")
    if not six_line(f):
        s += 0.05
        reasons.append("not listed on SIX")
    if p.get("esg") == "prefer" and _has(f, "esg"):
        s -= 0.03
    return s, reasons


def shortlist(funds: list[dict], p: dict, per_role: int = 4) -> list[dict]:
    b = broker(p.get("broker"))
    roles = [("core", "Global core", _core)]
    if p.get("approach") == "home-bias" or (p.get("homeBias") or 0) > 0:
        roles.append(("home-bias", "Swiss home bias", lambda f, q: _ac(f) == "equity" and _has(f, "switzerland")
                      and not _has(f, "small-cap", "dividend", "value", "min-volatility", "esg")))
    if p.get("approach") in ("themes", "specific"):
        for key in p.get("themes") or []:
            if key in THEMES:
                roles.append(("satellite", key, lambda f, q, t=THEMES[key]: _ac(f) == "equity" and _has(f, *t)))
    if p.get("bonds") and p["bonds"] != "none":
        chf = p["bonds"] == "chf"
        roles.append(("bonds", "CHF bonds" if chf else "Global bonds, CHF-hedged",
                      lambda f, q: "fixed" in _ac(f) and (_has(f, "switzerland") if chf else _has(f, "chf-hedged"))
                      and not _has(f, "high-yield", "money-market")))
    def rank(flt, q: dict, n: int) -> list[dict]:
        cands = sorted(((score(f, q, b), f) for f in funds if _passes_common(f, q) and flt(f, q)), key=lambda x: x[0][0])
        seen, picked = set(), []
        for (s, reasons), f in cands:
            key = (f["provider"], _v(f, "benchmark"), _v(f, "hedged"), _v(f, "use_of_income"))
            if key in seen:
                continue
            seen.add(key)
            picked.append({"isin": f["isin"], "name": f["name"]["v"], "score": round(s, 3), "reasons": reasons})
            if len(picked) >= n:
                break
        return picked

    out = []
    for role, title, flt in roles:
        picked = rank(flt, p, per_role)
        if not picked:
            # nothing meets every preference: the closest alternatives, each giving up exactly one
            for when, relaxed, label in RELAX:
                if when(p):
                    for c in rank(flt, {**p, **relaxed}, 2):
                        f = next(x for x in funds if x["isin"] == c["isin"])
                        s, reasons = score(f, p, b)
                        picked.append({**c, "score": round(s, 3), "reasons": reasons, "differs_from_profile": label(p)})
            picked = picked[:per_role]
        out.append({"role": role, "title": title, "candidates": picked})
    return out
