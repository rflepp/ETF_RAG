"""The deterministic eval scorers in agent/eval_agent.py."""
import pytest

pytest.importorskip("langgraph")

from agent import eval_agent as ev  # noqa: E402


def out(**kw):
    base = {"reply": "", "picks": [], "sources": [], "tools": [], "repairs": 0, "warnings": [], "latency_s": 1.0}
    return {**base, **kw}


def test_expected_fund_matches_picks_or_reply():
    ref = {"isins_any": ["IE00B4L5Y983"]}
    assert ev.expected_fund(out(picks=["IE00B4L5Y983"]), ref)["score"] == 1
    assert ev.expected_fund(out(reply="see IE00B4L5Y983"), ref)["score"] == 1
    assert ev.expected_fund(out(picks=["CH0237935652"]), ref)["score"] == 0
    assert ev.expected_fund(out(), {"isins_any": []})["score"] is None


def test_grounding_scores():
    assert ev.grounded_first_try(out(repairs=1), {})["score"] == 0
    assert ev.grounded_final(out(warnings=["percentages not present: 9%"]), {})["score"] == 0
    assert ev.cites_sources(out(sources=["IE00B4L5Y983-kid#3"]), {"must_cite": True})["score"] == 1
    assert ev.cites_sources(out(), {"must_cite": False})["score"] is None


@pytest.mark.parametrize("reply, ok", [
    ("The factsheet and KID do not cover securities lending revenue.", True),
    ("I can only help with ETFs, not single stocks like Tesla.", True),
    ("Securities lending earns about 0.02% a year.", False),
])
def test_admits_gap(reply, ok):
    assert ev.admits_gap(out(reply=reply), {"admits_gap": True})["score"] == int(ok)


def test_admits_gap_fails_when_it_still_recommends():
    r = ev.admits_gap(out(reply="This is not covered, but buy this.", picks=["IE00B4L5Y983"]), {"admits_gap": True})
    assert r["score"] == 0
