"""Vanguard, Swiss site.

List + documents: one GraphQL call (www.ch.vanguard/gpx/graphql, needs X-Consumer-ID: ch0) for all
port ids that the products page embeds. Factsheet (FS) and PRIIPs KID (PRP) live on fund-docs.vanguard.com.
"""
from __future__ import annotations

import re

from ..extract.vanguard_factsheet import parse_factsheet
from ..net import Client
from ..schema import ExchangeListing, Fact, Fund
from .base import DocLink, Listing

PRODUCTS = "https://www.ch.vanguard/en/products"
GRAPHQL = "https://www.ch.vanguard/gpx/graphql"
HEADERS = {"X-Consumer-ID": "ch0", "apollographql-client-name": "gpx", "Accept": "application/json"}
SRC = "api:vanguard"
QUERY = """query EtfList($portIds: [String!]!) { funds(portIds: $portIds) {
  profile { portId fundFullName polarisPdtTypeIndicator assetClassificationLevel1 marketOfDomicile
    distributionStrategy currencyHedgingStrategy fundCurrency
    identifiers(altIds: ["ISIN"]) { altId altIdValue }
    listings { exchange fundCurrency stockExchangeMarketIdentifierCode
      identifiers(altIds: ["SIX Swiss Exchange Ticker", "TIDM", "Deutsche Boerse Ticker",
                           "NYSE Euronext Exchange Ticker", "Borsa Italiana Ticker", "Bolsa Ticker"]) { altId altIdValue } } }
  documentDetails(businessLines: ["TRTL"], countryCodes: ["CH"], languages: ["EN"], docTypes: ["FS", "PRP"]) {
    type path publishDate }
} }"""
MIC = {"XSWX": "SIX Swiss Exchange", "XLON": "London Stock Exchange", "XETR": "Deutsche Boerse Xetra",
       "XAMS": "Euronext Amsterdam", "XMIL": "Borsa Italiana", "XPAR": "Euronext Paris", "XMEX": "Bolsa Mexicana De Valores"}


def _title(s: str | None) -> str | None:
    return s.title() if s else None


def fund_from_profile(node: dict) -> Fund | None:
    p = node["profile"]
    isin = next((i["altIdValue"] for i in p.get("identifiers") or [] if i["altId"] == "ISIN"), None)
    if not isin:
        return None
    f = Fund(isin=isin, provider="Vanguard", name=Fact(v=p["fundFullName"], s=SRC),
             product_url=f"https://www.ch.vanguard/en/private-investor/product/etf/"
                         f"{(p.get('assetClassificationLevel1') or 'equity').lower()}/{p['portId']}")

    def fact(v):
        return Fact(v=v, s=SRC) if v is not None else None

    f.share_class_currency = fact(p.get("fundCurrency"))
    f.domicile = fact(_title(p.get("marketOfDomicile")))
    dist = (p.get("distributionStrategy") or "").lower()
    f.use_of_income = fact("Accumulating" if "accum" in dist else "Distributing" if "distrib" in dist else None)
    hedge = p.get("currencyHedgingStrategy") or ""
    f.hedged = fact(bool(hedge) and "no hedge" not in hedge.lower())
    f.asset_class = fact({"Bond": "Fixed Income"}.get(p.get("assetClassificationLevel1"), p.get("assetClassificationLevel1")))
    listings = []
    for l in p.get("listings") or []:
        ticker = next((i["altIdValue"] for i in l.get("identifiers") or []), None)
        mic = l.get("stockExchangeMarketIdentifierCode")
        listings.append(ExchangeListing(exchange=MIC.get(mic, l.get("exchange") or mic), ticker=ticker, ccy=l.get("fundCurrency")))
    if listings:
        f.listings = Fact(v=listings, s=SRC)
        six = next((l for l in listings if l.exchange == "SIX Swiss Exchange" and l.ticker), None)
        first = six or next((l for l in listings if l.ticker), None)
        f.ticker = fact(first.ticker if first else None)
    return f


class Vanguard:
    name = "vanguard"

    def list_funds(self, client: Client) -> list[Listing]:
        html = client.get(PRODUCTS).text
        m = re.search(r'portIds\\?":\\?"([0-9A-Z,]+)', html)
        if not m:
            raise RuntimeError("vanguard: port ids not found on products page")
        data = client.post_json(GRAPHQL, {"operationName": "EtfList", "query": QUERY,
                                          "variables": {"portIds": m.group(1).split(",")}}, headers=HEADERS).json()
        out = []
        for node in data["data"]["funds"]:
            if node["profile"].get("polarisPdtTypeIndicator") != "ETF":
                continue
            f = fund_from_profile(node)
            if f:
                out.append(Listing(fund=f, raw=node))
        return out

    def doc_links(self, client: Client, listing: Listing) -> list[DocLink]:
        out = []
        for d in listing.raw.get("documentDetails") or []:
            kind = {"FS": "factsheet", "PRP": "kid"}.get(d["type"])
            if kind and d.get("path", "").endswith(".pdf") and not any(x.doc_type == kind for x in out):
                out.append(DocLink(doc_type=kind, url=d["path"]))
        return out

    def apply_docs(self, client: Client, listing: Listing, docs: dict, pages: dict) -> None:
        if "factsheet" not in pages:
            return
        f = listing.fund
        facts, meta = parse_factsheet(pages["factsheet"], docs["factsheet"].doc_id)
        for k, v in facts.items():
            setattr(f, k, v)
        if "fund_size_ccy" in meta:
            f.fund_size_ccy = meta["fund_size_ccy"]
        docs["factsheet"].as_of = meta.get("as_of")
