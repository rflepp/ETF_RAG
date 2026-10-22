import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent import domain

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data" / "etfs.json"
PROFILES = [
    {"horizon": "long", "approach": "home-bias", "homeBias": 20, "em": "yes", "bonds": "global-hedged", "esg": "any",
     "income": "acc", "hedge": "none", "broker": "swissquote", "themes": []},
    {"horizon": "medium", "approach": "themes", "em": "no", "bonds": "none", "esg": "prefer", "income": "any",
     "hedge": "none", "broker": "ibkr", "themes": ["semiconductors", "healthcare"]},
    {"horizon": "short", "approach": "max-div", "em": "any", "bonds": "chf", "esg": "only", "income": "dist",
     "hedge": "all", "broker": "yuh", "themes": []},
    # no CHF-hedged all-world fund exists: core falls back to near matches
    {"horizon": "long", "approach": "max-div", "em": "yes", "bonds": "none", "esg": "any", "income": "acc",
     "hedge": "all", "broker": "swissquote", "themes": []},
]


@pytest.mark.skipif(not DATA.exists() or not shutil.which("node"), reason="needs published data and node")
def test_python_port_matches_js():
    js = json.loads(subprocess.check_output(["node", str(ROOT / "tests" / "js_shortlist.mjs"), str(DATA),
                                             json.dumps(PROFILES)], text=True))
    funds = json.loads(DATA.read_text())["funds"]
    for p, j in zip(PROFILES, js):
        py = domain.shortlist(funds, p)
        assert [r["role"] for r in py] == [r["role"] for r in j["roles"]]
        for a, b in zip(py, j["roles"]):
            assert [c["isin"] for c in a["candidates"]] == b["isins"], (p["approach"], a["role"])
            assert [c.get("differs_from_profile") for c in a["candidates"]] == b["differs"], (p["approach"], a["role"])
        bk = domain.broker(p["broker"])
        for c in j["costs"]:
            f = next(x for x in funds if x["isin"] == c["isin"])
            tc = domain.trade_costs(f, bk)
            assert tc["stamp_pct"] == c["stampPct"] and tc["fx_pct"] == c["fxPct"]
            first = domain.first_year_pct(f, tc)
            assert (first is None and c["first"] is None) or abs(first - c["first"]) < 1e-9


def test_stamp_duty_rules():
    ie = {"isin": "IE00B4L5Y983", "domicile": {"v": "Ireland"}}
    ch = {"isin": "CH0237935652", "domicile": {"v": "Switzerland"}}
    assert domain.trade_costs(ie, domain.broker("swissquote"))["stamp_pct"] == 0.15
    assert domain.trade_costs(ch, domain.broker("swissquote"))["stamp_pct"] == 0.075
    assert domain.trade_costs(ie, domain.broker("ibkr"))["stamp_pct"] == 0


def test_near_matches_when_no_fund_meets_every_preference():
    funds = [
        {"isin": "IE0000000001", "name": {"v": "World CHF-hedged"}, "provider": "x", "asset_class": {"v": "Equity"},
         "theme_tags": ["equity", "world", "chf-hedged", "currency-hedged"], "use_of_income": {"v": "Accumulating"}},
        {"isin": "IE0000000002", "name": {"v": "All-World"}, "provider": "y", "asset_class": {"v": "Equity"},
         "theme_tags": ["equity", "all-world"], "use_of_income": {"v": "Accumulating"}},
    ]
    p = {"approach": "max-div", "em": "yes", "hedge": "all", "income": "acc", "esg": "any", "broker": "ibkr"}
    core = domain.shortlist(funds, p)[0]["candidates"]
    assert [(c["isin"], c["differs_from_profile"]) for c in core] == [
        ("IE0000000001", "no emerging markets"), ("IE0000000002", "not hedged to CHF")]
    exact = domain.shortlist(funds, {**p, "hedge": "none"})[0]["candidates"]
    assert [c["isin"] for c in exact] == ["IE0000000002"] and "differs_from_profile" not in exact[0]
