"""Fakes for the agent tests: a small in-memory fund universe instead of Qdrant, and a scripted chat model."""
from __future__ import annotations

import itertools


def _fact(v, isin, doc="factsheet", page=1):
    return {"v": v, "s": f"{isin}-{doc}", "p": page}


def _fund(isin, name, provider, domicile, ter, td5, tags, six_ccy="CHF"):
    return {
        "isin": isin, "provider": provider, "name": {"v": name, "s": "api:x"}, "product_url": f"https://example.com/{isin}",
        "docs": [{"doc_id": f"{isin}-factsheet", "doc_type": "factsheet", "url": f"https://example.com/{isin}/fs.pdf"},
                 {"doc_id": f"{isin}-kid", "doc_type": "kid", "url": f"https://example.com/{isin}/kid.pdf"}],
        "asset_class": {"v": "Equity", "s": "api:x"}, "domicile": _fact(domicile, isin),
        "use_of_income": _fact("Accumulating", isin), "hedged": _fact(False, isin),
        "replication": _fact("physical-full", isin), "ter": _fact(ter, isin), "sri": _fact(4, isin, "kid", 2),
        "fund_size_m": _fact(1000.0, isin), "td_avg_5y": td5, "td_avg_3y": td5, "theme_tags": tags,
        "listings": _fact([{"exchange": "SIX Swiss Exchange", "ticker": "T", "ccy": six_ccy}], isin, page=3),
    }


FUNDS = [
    _fund("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF", "iShares", "Ireland", 0.2, 0.07, ["world", "equity"], "USD"),
    _fund("CH0237935652", "iShares Core SPI ETF (CH)", "iShares", "Switzerland", 0.1, -0.1, ["switzerland", "equity"]),
    _fund("IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF", "Vanguard", "Ireland", 0.22, 0.05, ["all-world", "equity"]),
]


class FakeRetriever:
    def __init__(self, funds=FUNDS):
        from agent.retrieval import Passage
        self.funds = {f["isin"]: f for f in funds}
        self.passages = [Passage(id=f"{i}-kid#1", isin=i, name=f["name"]["v"], doc_type="kid", page=2,
                                 text=f"Transaction costs of {f['name']['v']} are low.", url=f["docs"][1]["url"], score=1.0)
                         for i, f in self.funds.items()]

    def search_funds(self, query, flt=None, k=8, **_):
        return [{"isin": f["isin"], "name": f["name"]["v"], "ter": f["ter"]["v"], "td_avg_5y": f["td_avg_5y"],
                 "td_avg_3y": f["td_avg_3y"], "fund_size_m": 1000.0, "replication": "physical-full",
                 "use_of_income": "Accumulating", "hedged": False, "six_ccy": "CHF", "tags": f["theme_tags"]}
                for f in self.funds.values()][:k]

    def search_passages(self, query, isins=None, doc_types=None, k=8, **_):
        return [p for p in self.passages if not isins or p.isin in isins][:k]

    def get_funds(self, isins):
        return {i: self.funds[i] for i in isins if i in self.funds}

    def all_funds(self):
        return list(self.funds.values())


class ScriptedModel:
    """Stands in for a tool-bound chat model: returns the scripted messages in order and records what it was sent."""

    def __init__(self, *script):
        self.script = list(script)
        self.seen: list[list] = []

    def invoke(self, messages, *_, **__):
        self.seen.append(messages)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


_ids = itertools.count()


def call(name: str, **args):
    from langchain_core.messages import AIMessage
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call{next(_ids)}"}])


def final(reply: str, picks=None, **kw):
    return call("final_answer", reply=reply, picks=picks or [], **kw)
