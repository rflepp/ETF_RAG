import numpy as np

from pipeline.chunk import chunk_doc
from pipeline.derive import theme_tags, tracking_difference
from pipeline.embed import quantize
from pipeline.extract.ishares_factsheet import key_values, replication
from pipeline.extract.vanguard_factsheet import calendar_returns as vg_calendar
from pipeline.parse import Page
from pipeline.providers.ishares import aggregate, parse_holdings_csv
from pipeline.schema import Fact, Fund, YearReturn
from pipeline.validate import isin_ok


def fund(**kw) -> Fund:
    return Fund(isin="IE00B4L5Y983", provider="t", name=Fact(v=kw.pop("name", "Test ETF"), s="x"),
                product_url="u", **kw)


def test_isin_checksum():
    assert isin_ok("IE00B4L5Y983")
    assert isin_ok("CH0237935652")
    assert not isin_ok("IE00B4L5Y984")
    assert not isin_ok("IE00B4L5Y98")


def test_tracking_difference_needs_full_windows():
    rets = [YearReturn(year=y, fund=f, bench=b) for y, f, b in
            [(2021, None, None), (2022, -18.0, -18.1), (2023, 23.0, 23.2), (2024, 18.0, 18.1)]]
    f = fund(calendar_returns=Fact(v=rets, s="x"))
    tracking_difference(f)
    assert f.td_avg_3y == round((0.1 - 0.2 - 0.1) / 3, 3)
    assert f.td_avg_5y is None


def test_tracking_difference_skips_gaps():
    rets = [YearReturn(year=y, fund=1.0, bench=1.0) for y in (2019, 2020, 2022)]
    f = fund(calendar_returns=Fact(v=rets, s="x"))
    tracking_difference(f)
    assert f.td_avg_3y is None  # 2019..2022 is not three consecutive years


def test_key_values_wraps_continuation_lines():
    ls = [(1, "Benchmark : MSCI ACWI IMI Semiconductors &"), (1, "Semiconductor Equipment ESG Screened"),
          (1, "Capped"), (1, "Fund Launch Date : 03-Aug-2021"), (1, "PORTFOLIO CHARACTERISTICS")]
    kv = key_values(ls)
    assert kv["Benchmark"][0] == "MSCI ACWI IMI Semiconductors & Semiconductor Equipment ESG Screened Capped"
    assert kv["Fund Launch Date"][0] == "03-Aug-2021"


def test_replication_mapping():
    assert replication("Optimised", "Physical") == "physical-sampling"
    assert replication("Replicated", "Physical") == "physical-full"
    assert replication("Replicated", "Synthetic") == "synthetic"


def test_holdings_rounding_falls_back_to_market_value():
    rows = "\n".join(f'"T{i}","N{i}","Gov","Fixed Income","1’000.00","0.00","Japan"' for i in range(99))
    csv = ('﻿Fund Holdings as of,"15/Oct/2026"\n \n'
           'Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Location\n'
           '"A","Big","Gov","Fixed Income","1’000.00","1.00","United States"\n' + rows)
    as_of, parsed = parse_holdings_csv(csv)
    assert as_of == "2026-10-15"
    total = sum(r["_w"] for r in parsed)
    assert abs(total - 100) < 1e-6
    assert dict(aggregate(parsed, "Location"))["Japan"] == 99.0


def test_vanguard_calendar_placeholders():
    ls = [(2, "Performance summary"), (2, "Benchmark — X"), (2, "—"), (2, "—"),
          (2, "01 Jan 2024"), (2, "-"), (2, "31 Dec 2024"), (2, "01 Jan 2025"), (2, "-"), (2, "31 Dec 2025"),
          (2, "Fund (Net of expenses)"), (2, "—"), (2, "—"), (2, "17.19%"), (2, "22.56%"),
          (2, "Benchmark"), (2, "—"), (2, "—"), (2, "17.20%"), (2, "22.62%"), (2, "Rolling 12-month")]
    rows, page = vg_calendar(ls)
    assert [(r.year, r.fund, r.bench) for r in rows] == [(2024, 17.19, 17.2), (2025, 22.56, 22.62)]


def test_quantize_preserves_ranking():
    rng = np.random.default_rng(0)
    m = rng.normal(size=(50, 384)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    q, s = quantize(m)
    approx = q.astype(np.float32) * s[:, None]
    query = m[7]
    assert int(np.argmax(approx @ query)) == 7
    assert np.max(np.abs(approx - m)) < 0.01


def test_chunker_drops_running_header_and_legal_tail():
    head = "My Fund UCITS ETF\nU.S. Dollar (Accumulating)\n"
    body = " ".join(["The fund invests in global equities and aims to track its index closely."] * 6)
    pages = [Page(n=1, text=head + "FUND OVERVIEW\n" + body),
             Page(n=2, text=head + "KEY RISKS\n" + body),
             Page(n=3, text=head + "IMPORTANT INFORMATION\nLegal text that should never be indexed. " * 20)]
    chunks = chunk_doc(pages, "D", "factsheet", "My Fund")
    assert chunks and all("Legal text" not in c.text for c in chunks)
    assert all("U.S. Dollar (Accumulating)" not in c.text for c in chunks)
    assert {c.page for c in chunks} == {1, 2}


def test_theme_tags():
    f = fund(name="iShares MSCI Global Semiconductors UCITS ETF", asset_class=Fact(v="Equity", s="x"))
    tags = theme_tags(f)
    assert "semiconductors" in tags and "equity" in tags


def test_holdings_csv_reads_only_first_section():
    section = ('Fund Holdings as of,"15/Oct/2026"\n \nTicker,Name,Sector,Asset Class,Market Value,Weight (%),Location\n'
               '"A","A","IT","Equity","60","60.00","United States"\n"B","B","IT","Equity","40","40.00","Japan"\n \n')
    _, rows = parse_holdings_csv(section + section)
    assert len(rows) == 2 and sum(r["_w"] for r in rows) == 100


def test_active_fund_has_no_tracking_difference():
    rets = [YearReturn(year=y, fund=10.0, bench=8.0) for y in (2023, 2024, 2025)]
    f = fund(calendar_returns=Fact(v=rets, s="x"), replication=Fact(v="active", s="x"))
    tracking_difference(f)
    assert f.td_avg_3y is None
