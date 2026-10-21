"""iShares (BlackRock), Swiss individual-investor site.

List: the product screener JSON (one call, all share classes incl. index funds; we keep ETFs).
Docs: each product page has a literature fragment at <productPageUrl>/1495092304810.ajax?action=ajax.
PDF links need siteEntryPassthrough=true, otherwise the site answers with the investor-type gate page.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime, timezone

from ..extract.ishares_factsheet import parse_factsheet
from ..net import Client
from ..schema import Doc, Fact, Fund, Weight
from .base import DocLink, Listing

BASE = "https://www.ishares.com"
SCREENER = (BASE + "/ch/individual/en/product-screener/product-screener-v3.1.jsn"
            "?dcrPath=/templatedata/config/product-screener-v3/data/en/ch/product-screener/"
            "ishares-product-screener-backend-config&siteEntryPassthrough=true")
LITERATURE_ID = "1495092304810"
SRC = "api:ishares"


def _num(x) -> float | None:
    if isinstance(x, dict):
        x = x.get("r")
    return x if isinstance(x, (int, float)) else None


def _str(x) -> str | None:
    if not isinstance(x, str):
        return None
    x = x.strip()
    return None if x in ("", "-") else x


def _date(x) -> str | None:
    r = x.get("r") if isinstance(x, dict) else None
    if not isinstance(r, int):
        return None
    s = str(r)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()


def fund_from_screener(row: dict) -> Fund:
    isin = row["isin"]
    url = BASE + row["productPageUrl"]
    f = Fund(isin=isin, provider="iShares", name=Fact(v=row["fundName"], s=SRC), product_url=url)

    def fact(val):
        return Fact(v=val, s=SRC) if val is not None else None

    f.ticker = fact(_str(row.get("localExchangeTicker")))
    f.base_currency = fact(_str(row.get("seriesBaseCurrencyCode")))
    f.domicile = fact(_str(row.get("domicile")))
    f.use_of_income = fact(_str(row.get("useOfProfits")))
    f.inception = fact(_date(row.get("inceptionDate")))
    f.asset_class = fact(_str(row.get("aladdinAssetClass")))
    f.sub_asset_class = fact(_str(row.get("aladdinSubAssetClass")))
    f.region = fact(_str(row.get("aladdinRegion")))
    f.market_type = fact(_str(row.get("aladdinMarketType")))
    f.sfdr = fact(_str(row.get("aladdinSfdr")))
    ter = _num(row.get("ter")) or _num(row.get("ter_ocf"))
    f.ter = fact(ter)
    size = _num(row.get("totalFundSizeInMillions"))
    if size is not None:
        f.fund_size_m = fact(round(size, 2))
        f.fund_size_ccy = _str(row.get("seriesBaseCurrencyCode"))
    return f


class IShares:
    name = "ishares"

    def list_funds(self, client: Client) -> list[Listing]:
        data = client.get(SCREENER).json()
        out = []
        for row in data.values():
            if "etf" not in row.get("productView", []) or not row.get("isin"):
                continue
            if re.search(r"\bET[PC]\b", row.get("fundName", "")):
                continue  # exchange-traded products/commodities (e.g. Bitcoin ETP) are not UCITS funds
            out.append(Listing(fund=fund_from_screener(row), raw=row))
        return out

    def doc_links(self, client: Client, listing: Listing) -> list[DocLink]:
        url = listing.fund.product_url.split("?")[0] + f"/{LITERATURE_ID}.ajax?action=ajax"
        html = client.get(url, headers={"X-Requested-With": "XMLHttpRequest"}).text
        return pick_docs(html)

    def apply_docs(self, client: Client, listing: Listing, docs: dict, pages: dict) -> None:
        f = listing.fund
        if "factsheet" in pages:
            facts, meta = parse_factsheet(pages["factsheet"], docs["factsheet"].doc_id)
            for k, v in facts.items():
                setattr(f, k, v)  # citable PDF facts override the screener's
            if "fund_size_ccy" in meta:
                f.fund_size_ccy = meta["fund_size_ccy"]
            docs["factsheet"].as_of = meta.get("as_of")
        if f.replication is None and "Active" in str(listing.raw.get("investmentStyle", "")):
            f.replication = Fact(v="active", s=SRC)
        from ..download import ROOT, fresh  # lazy: download imports the providers package

        try:
            url = holdings_url(listing)
            cache = ROOT / "data" / "cache" / "holdings" / f"{f.isin}.csv"
            mtime = datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc).isoformat() if cache.exists() else None
            if fresh({"fetched_at": mtime}, cache):
                text = cache.read_text()
            else:
                text = client.get(url).text
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text)
            as_of, rows = parse_holdings_csv(text)
        except RuntimeError as e:
            f.issues.append(f"holdings csv: {e}")
            return
        if not rows:
            return
        doc = Doc(doc_id=f"{f.isin}-holdings", isin=f.isin, doc_type="holdings", url=url,
                  sha256=hashlib.sha256(text.encode()).hexdigest(), as_of=as_of)
        f.docs.append(doc)
        f.sectors = Fact(v=[Weight(name=n, w=w) for n, w in aggregate(rows, "Sector")], s=doc.doc_id)
        f.countries = Fact(v=[Weight(name=n, w=w) for n, w in aggregate(rows, "Location")], s=doc.doc_id)


def pick_docs(html: str) -> list[DocLink]:
    """Choose the English Swiss factsheet and the PRIIPs KID among the literature links."""
    hrefs = re.findall(r'href="([^"]+\.pdf)[^"]*"', html)
    fs = [h for h in hrefs if "/fact-sheet/" in h]
    kid = [h for h in hrefs if "/kiid/" in h or "/kid/" in h]

    def best(cands: list[str], prefs: list[str]) -> str | None:
        for p in prefs:
            for c in cands:
                if p in c:
                    return c
        return cands[0] if cands else None

    out = []
    f = best(fs, ["-en-ch.pdf", "-en-gb.pdf", "-en."])
    k = best(kid, ["priips", "-en.pdf"])
    for doc_type, h in (("factsheet", f), ("kid", k)):
        if h:
            u = h if h.startswith("http") else BASE + h
            out.append(DocLink(doc_type=doc_type, url=u + "?siteEntryPassthrough=true"))
    return out


HOLDINGS_ID = "1495092304805"


def holdings_url(listing: Listing) -> str:
    return listing.fund.product_url.split("?")[0] + f"/{HOLDINGS_ID}.ajax?fileType=csv&fileName=holdings&dataType=fund"


def parse_holdings_csv(text: str) -> tuple[str | None, list[dict]]:
    """The CSV starts with a few preamble lines ("Fund Holdings as of", ...) before the header row."""
    text = text.lstrip("﻿")
    as_of = None
    if m := re.search(r'Fund Holdings as of,"([^"]+)"', text):
        try:
            as_of = datetime.strptime(m.group(1), "%d/%b/%Y").date().isoformat()
        except ValueError:
            pass
    start = text.find("Ticker,")
    if start < 0:
        return as_of, []
    # Some files repeat the whole table a second time; read only the first section.
    end = text.find("Fund Holdings as of", start)
    rows = []
    for r in csv.DictReader(io.StringIO(text[start:end if end > 0 else None])):
        w = (r.get("Weight (%)") or "").replace("’", "").replace(",", "")
        try:
            r["_w"] = float(w)
        except ValueError:
            continue
        mv = (r.get("Market Value") or "").replace("’", "").replace(",", "")
        try:
            r["_mv"] = float(mv)
        except ValueError:
            r["_mv"] = None
        rows.append(r)
    # Weights are rounded to 0.01; for funds with thousands of tiny positions the rounded weights
    # no longer add up, so fall back to market-value weights.
    total_w = sum(r["_w"] for r in rows)
    total_mv = sum(r["_mv"] for r in rows if r["_mv"] is not None)
    if rows and total_w < 95 and total_mv > 0 and all(r["_mv"] is not None for r in rows):
        for r in rows:
            r["_w"] = r["_mv"] / total_mv * 100
    return as_of, rows


def aggregate(rows: list[dict], key: str, top: int = 15) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for r in rows:
        k = (r.get(key) or "-").strip() or "-"
        acc[k] = acc.get(k, 0.0) + r["_w"]
    items = sorted(acc.items(), key=lambda kv: -kv[1])
    head, tail = items[:top], items[top:]
    if tail:
        head.append(("Other", sum(w for _, w in tail)))
    return [(k, round(w, 2)) for k, w in head]

