"""Agent tools. Each returns (content for the model, artifact) where the artifact registers every citable id
in the evidence registry: passages ("IE00B4L5Y983-kid#12") and facts ("IE00B4L5Y983-factsheet#p1").
The verify node later accepts only citations and ISINs that went through this registry."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from . import domain
from .retrieval import FundFilter, Retriever
from .sql import SCHEMA, FundsDB, SQLError

FACT_FIELDS = ["ter", "kid_ongoing_pct", "kid_transaction_pct", "kid_one_off_secondary_exempt", "sri", "replication",
               "use_of_income", "domicile", "share_class_currency", "hedged", "hedge_currency", "benchmark",
               "inception", "fund_size_m", "holdings_count", "listings", "top_holdings", "sectors", "countries", "sfdr"]
TAGS = sorted({t for ts in domain.THEMES.values() for t in ts} | domain.NARROW | {
    "world", "all-world", "usa", "europe", "switzerland", "japan", "emerging-markets", "esg", "government-bonds",
    "corporate-bonds", "high-yield", "inflation-linked", "aggregate-bonds", "short-duration", "chf-hedged",
    "currency-hedged", "active", "equity", "bonds"})


@dataclass
class Ctx:
    retriever: Retriever
    profile: dict = field(default_factory=dict)
    funds_cache: list | None = None
    db_cache: FundsDB | None = None

    def funds(self) -> list[dict]:
        if self.funds_cache is None:
            self.funds_cache = self.retriever.all_funds()
        return self.funds_cache

    def db(self) -> FundsDB:
        if self.db_cache is None:
            self.db_cache = FundsDB(self.funds())
        return self.db_cache


def fact_id(isin: str, fact: dict) -> str:
    s = fact.get("s", "")
    if s.startswith("api:"):
        return f"{isin}-site"
    return f"{s}#p{fact.get('p') or 1}"


def _url_for(f: dict, sid: str) -> tuple[str | None, int | None]:
    if sid.endswith("-site"):
        return f.get("product_url"), None
    doc_id, _, page = sid.partition("#p")
    d = next((d for d in f.get("docs", []) if d["doc_id"] == doc_id), None)
    return (d or {}).get("url"), int(page) if page.isdigit() else None


def _fmt(v):
    if isinstance(v, list):
        if v and isinstance(v[0], dict) and "w" in v[0]:
            return ", ".join(f"{x['name']} {x['w']:.1f}%" for x in v[:6])
        if v and isinstance(v[0], dict) and "exchange" in v[0]:
            return "; ".join(f"{x['exchange']}: {x.get('ticker')} in {x.get('ccy')}" for x in v)
    return v


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


# ---------- argument schemas (these become the JSON schemas the model sees) ----------

class SearchFundsArgs(BaseModel):
    query: str = Field(description="What the investor is looking for, e.g. 'global semiconductor companies'")
    asset_class: str | None = Field(None, description="'Equity', 'Fixed Income', 'Multi Asset' or 'Real Estate'")
    tags_any: list[str] | None = Field(None, description=f"Keep funds with at least one of these tags: {', '.join(TAGS)}")
    exclude_tags: list[str] | None = Field(None, description="Drop funds with any of these tags")
    use_of_income: str | None = Field(None, description="'Accumulating' or 'Distributing'")
    hedged: bool | None = Field(None, description="True for currency-hedged share classes only, False to exclude them")
    max_ter: float | None = Field(None, description="Maximum TER in percent, e.g. 0.25")
    k: int = Field(6, ge=1, le=10)


class FactsArgs(BaseModel):
    isins: list[str] = Field(description="Up to 6 ISINs", max_length=6)
    fields: list[str] | None = Field(None, description=f"Subset of: {', '.join(FACT_FIELDS)}. Default: all")


class SearchDocsArgs(BaseModel):
    query: str = Field(description="What to look up in the fund documents, e.g. 'transaction costs' or 'currency risk'")
    isins: list[str] | None = Field(None, description="Restrict to these funds (recommended)")
    doc_types: list[str] | None = Field(None, description="'factsheet', 'kid' and/or 'holdings'")
    k: int = Field(6, ge=1, le=10)


class CostsArgs(BaseModel):
    isins: list[str] = Field(description="Up to 6 ISINs to compare", max_length=6)
    broker: str | None = Field(None, description="ibkr, saxo, swissquote, yuh, postfinance, neon, other_ch, other_foreign. "
                                                 "Default: the investor's broker from the profile")
    amount_chf: float | None = Field(None, description="Amount invested; default from the profile")
    horizon_years: int | None = Field(None, description="Holding period for spreading one-off costs")


class SQLArgs(BaseModel):
    sql: str = Field(description="One SQLite SELECT statement over the funds / fund_tags tables")


class ShortlistArgs(BaseModel):
    per_role: int = Field(4, ge=1, le=6, description="Candidates per role")


def make_tools(ctx: Ctx) -> list[StructuredTool]:
    def search_funds(query: str, asset_class=None, tags_any=None, exclude_tags=None, use_of_income=None, hedged=None,
                     max_ter=None, k=6):
        flt = FundFilter(asset_class=asset_class, tags_any=tags_any, exclude_tags=exclude_tags,
                         use_of_income=use_of_income, hedged=hedged, max_ter=max_ter)
        hits = ctx.retriever.search_funds(query, flt, k=k, rerank=False)
        rows = [{"isin": h["isin"], "name": h["name"], "ter": h["ter"], "td_5y": h["td_avg_5y"], "td_3y": h["td_avg_3y"],
                 "size_m": h["fund_size_m"], "replication": h["replication"], "income": h["use_of_income"],
                 "hedged": h["hedged"], "six_ccy": h["six_ccy"], "tags": h["tags"][:8]} for h in hits]
        return _dump(rows or "no fund matches these filters"), {"isins": [r["isin"] for r in rows], "evidence": {}}

    def get_fund_facts(isins: list[str], fields=None):
        funds = ctx.retriever.get_funds(isins[:6])
        out, ev = {}, {}
        for isin in isins[:6]:
            f = funds.get(isin)
            if not f:
                out[isin] = "unknown ISIN"
                continue
            row = {"name": f["name"]["v"], "provider": f["provider"], "td_avg_3y": f.get("td_avg_3y"),
                   "td_avg_5y": f.get("td_avg_5y"), "tags": f.get("theme_tags", [])}
            for k in fields or FACT_FIELDS:
                fact = f.get(k)
                if not isinstance(fact, dict) or fact.get("v") is None:
                    continue
                sid = fact_id(isin, fact)
                row[k] = [_fmt(fact["v"]), sid]
                url, page = _url_for(f, sid)
                ev.setdefault(sid, {"kind": "fact", "isin": isin, "name": f["name"]["v"], "url": url, "page": page,
                                    "text": ""})
                ev[sid]["text"] += f"{k}: {_fmt(fact['v'])}; "
            out[isin] = row
        return _dump(out), {"isins": list(funds), "evidence": ev}

    def search_documents(query: str, isins=None, doc_types=None, k=6):
        ps = ctx.retriever.search_passages(query, isins=isins, doc_types=doc_types, k=k, per_fund=3)
        rows = [{"id": p.id, "fund": p.name, "doc": p.doc_type, "page": p.page, "text": p.text[:900]} for p in ps]
        ev = {p.id: {"kind": "passage", "isin": p.isin, "name": p.name, "url": p.url, "page": p.page, "text": p.text}
              for p in ps}
        return _dump(rows or "nothing relevant found in the documents"), {"isins": [p.isin for p in ps], "evidence": ev}

    def compare_costs(isins: list[str], broker=None, amount_chf=None, horizon_years=None):
        b = domain.broker(broker or ctx.profile.get("broker"))
        amount = amount_chf or ctx.profile.get("amount")
        horizon = horizon_years or {"long": 10, "medium": 7, "short": 3}.get(ctx.profile.get("horizon"), 10)
        funds = ctx.retriever.get_funds(isins[:6])
        rows, ev = [], {}
        for isin, f in funds.items():
            tc = domain.trade_costs(f, b)
            first = domain.first_year_pct(f, tc)
            eff, basis = domain.effective_annual_cost(f)
            one_off = tc["stamp_pct"] + (tc["fx_pct"] or 0) + (tc["commission_pct"] or 0)
            ter = f.get("ter") or {}
            sid = fact_id(isin, ter) if ter else None
            if sid:
                url, page = _url_for(f, sid)
                ev.setdefault(sid, {"kind": "fact", "isin": isin, "name": f["name"]["v"], "url": url, "page": page,
                                    "text": f"ter: {ter.get('v')}; "})
            rows.append({
                "isin": isin, "name": f["name"]["v"], "ter": [ter.get("v"), sid],
                "annual_cost_measure": {"pct": round(eff, 3), "basis": {"td5": "5y tracking difference", "td3":
                                        "3y tracking difference", "ter": "TER + 0.05 (no tracking data)"}.get(basis)},
                "stamp_duty_pct": tc["stamp_pct"], "fx_pct": tc["fx_pct"], "fx_currency": tc["fx_ccy"],
                "commission_pct": tc["commission_pct"], "one_off_pct": round(one_off, 3),
                "first_year_pct": None if first is None else round(first, 3),
                "first_year_chf": None if (first is None or not amount) else round(amount * first / 100),
                f"cost_over_{horizon}y_pct": round(eff * horizon + one_off, 3),
                "six_listing": tc["six"],
            })
        rows.sort(key=lambda r: r[f"cost_over_{horizon}y_pct"])
        note = rules_note(b)
        return _dump({"broker": b["label"], "horizon_years": horizon, "note": note, "funds": rows}), \
            {"isins": list(funds), "evidence": ev}

    def query_funds_sql(sql: str):
        try:
            cols, rows, truncated = ctx.db().query(sql)
        except SQLError as e:
            return f"SQL error: {e}. Only SELECT on funds / fund_tags is allowed.", {"isins": [], "evidence": {}}
        out = {"columns": cols, "rows": rows}
        if truncated:
            out["note"] = f"truncated to {len(rows)} rows; add ORDER BY / LIMIT"
        isins = [r[cols.index("isin")] for r in rows] if "isin" in cols else []
        return _dump(out if rows else "no rows"), {"isins": isins, "evidence": {}}

    def shortlist(per_role: int = 4):
        if not ctx.profile:
            return "no investor profile available; use search_funds instead", {"isins": [], "evidence": {}}
        roles = domain.shortlist(ctx.funds(), ctx.profile, per_role=per_role)
        isins = [c["isin"] for r in roles for c in r["candidates"]]
        return _dump(roles), {"isins": isins, "evidence": {}}

    specs = [
        (search_funds, SearchFundsArgs, "search_funds",
         "Find ETFs matching a need (theme, region, asset class) with optional hard filters. Returns candidates with "
         "key numbers. Use to discover funds that are not yet in the conversation."),
        (get_fund_facts, FactsArgs, "get_fund_facts",
         "Exact facts for specific ETFs (costs, structure, exposure, listings), each value with a citable source id."),
        (search_documents, SearchDocsArgs, "search_documents",
         "Search the funds' factsheets, KIDs and holdings for passages answering a question (risks, costs, "
         "methodology, objectives, performance). Returns passages with citable ids."),
        (compare_costs, CostsArgs, "compare_costs",
         "Total cost comparison for a Swiss investor: TER, tracking difference, Swiss stamp duty and FX for the "
         "broker, first-year cost in CHF and cost over the holding period. Sorted cheapest first."),
        (query_funds_sql, SQLArgs, "query_funds_sql",
         "Screen, count or rank funds with exact numeric conditions using one read-only SQLite SELECT, e.g. "
         "TER below a threshold, fund size above X, sorted by tracking difference. Translate every condition of "
         "the question (region or tag, asset class, income, size) into WHERE, put the ranking into ORDER BY and "
         "add LIMIT 10. Select isin and name so the funds can be recommended; values are not citable, use "
         "get_fund_facts for citations. Schema:\n" + SCHEMA),
        (shortlist, ShortlistArgs, "shortlist",
         "Rule-based starting shortlist per portfolio role (core, home bias, themes, bonds) built from the "
         "investor's interview profile. A good first step when the investor has completed the interview."),
    ]
    return [StructuredTool.from_function(func=fn, name=name, description=desc, args_schema=schema,
                                         response_format="content_and_artifact") for fn, schema, name, desc in specs]


def rules_note(b: dict) -> str:
    r = domain.rules()
    sd = "Swiss stamp duty applies (Swiss broker)" if b["swiss_dealer"] else "no Swiss stamp duty (foreign broker)"
    return f"{sd}. {r['fx_note']}"
