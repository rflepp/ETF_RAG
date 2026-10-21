"""Data model. Every extracted value is a Fact that remembers where it came from."""
from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DocType = Literal["factsheet", "kid", "holdings"]


class Fact(BaseModel, Generic[T]):
    v: T
    s: str          # source id: a doc_id (e.g. "IE00B4L5Y983-factsheet") or "api:<provider>"
    p: int | None = None  # 1-based page in that doc


class Doc(BaseModel):
    doc_id: str
    isin: str
    doc_type: DocType
    url: str
    sha256: str | None = None
    as_of: str | None = None
    pages: int | None = None


class Weight(BaseModel):
    name: str
    w: float


class ExchangeListing(BaseModel):
    exchange: str
    ticker: str | None = None
    ccy: str | None = None


class YearReturn(BaseModel):
    year: int
    fund: float | None
    bench: float | None


class Fund(BaseModel):
    isin: str
    provider: str
    name: Fact[str]
    product_url: str
    docs: list[Doc] = Field(default_factory=list)

    # identity / structure
    ticker: Fact[str] | None = None
    share_class_currency: Fact[str] | None = None
    base_currency: Fact[str] | None = None
    domicile: Fact[str] | None = None
    use_of_income: Fact[str] | None = None          # Accumulating | Distributing
    hedged: Fact[bool] | None = None
    hedge_currency: Fact[str] | None = None
    replication: Fact[str] | None = None            # physical-full | physical-sampling | synthetic
    benchmark: Fact[str] | None = None
    inception: Fact[str] | None = None              # ISO date of share class launch
    asset_class: Fact[str] | None = None
    sub_asset_class: Fact[str] | None = None
    region: Fact[str] | None = None
    market_type: Fact[str] | None = None            # Developed | Emerging | ...
    holdings_count: Fact[int] | None = None
    securities_lending: Fact[bool] | None = None
    sfdr: Fact[str] | None = None
    listings: Fact[list[ExchangeListing]] | None = None

    # size
    fund_size_m: Fact[float] | None = None          # in fund_size_ccy, millions
    fund_size_ccy: str | None = None

    # costs
    ter: Fact[float] | None = None                  # % p.a.
    kid_ongoing_pct: Fact[float] | None = None
    kid_transaction_pct: Fact[float] | None = None
    kid_entry_pct: Fact[float] | None = None
    kid_exit_pct: Fact[float] | None = None
    kid_one_off_secondary_exempt: Fact[bool] | None = None  # entry/exit fees do not apply on exchange
    sri: Fact[int] | None = None                    # PRIIPs summary risk indicator 1..7
    rhp_years: Fact[int] | None = None              # PRIIPs recommended holding period

    # performance (calendar years, % in share class currency)
    calendar_returns: Fact[list[YearReturn]] | None = None
    td_avg_3y: float | None = None                  # derived, fund minus benchmark, pp per year
    td_avg_5y: float | None = None

    # composition
    top_holdings: Fact[list[Weight]] | None = None
    sectors: Fact[list[Weight]] | None = None
    countries: Fact[list[Weight]] | None = None

    theme_tags: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)  # validation problems, shown in the review list
