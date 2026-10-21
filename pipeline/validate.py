"""Sanity checks. Problems are recorded on the fund (f.issues) and end up in the review list."""
from __future__ import annotations

from datetime import date

from .schema import Fund

REQUIRED = ["ter", "domicile", "replication", "benchmark", "use_of_income", "share_class_currency"]


def isin_ok(isin: str) -> bool:
    if len(isin) != 12 or not isin[:2].isalpha() or not isin[-1].isdigit():
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1].upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(isin[-1])


def validate(f: Fund, api_ter: float | None = None) -> list[str]:
    issues: list[str] = []
    if not isin_ok(f.isin):
        issues.append("bad ISIN checksum")
    multi_asset = f.asset_class is not None and "multi" in f.asset_class.v.lower()
    for field in REQUIRED:
        if field == "benchmark" and multi_asset:
            continue  # funds of funds with a strategic allocation have no single benchmark
        if getattr(f, field) is None:
            issues.append(f"missing {field}")
    if f.ter is not None and not (0 <= f.ter.v <= 1.5):
        issues.append(f"TER out of range: {f.ter.v}")
    if api_ter is not None and f.ter is not None and abs(api_ter - f.ter.v) > 0.005:
        issues.append(f"TER mismatch: factsheet {f.ter.v} vs provider list {api_ter}")
    if f.sri is not None and not (1 <= f.sri.v <= 7):
        issues.append(f"SRI out of range: {f.sri.v}")
    for name in ("sectors", "countries"):
        fact = getattr(f, name)
        if fact is not None:
            s = sum(w.w for w in fact.v)
            if not (95 <= s <= 105):
                issues.append(f"{name} weights sum to {s:.1f}%")
    if f.calendar_returns is None and _older_than_years(f, 2):
        issues.append("missing calendar returns")  # young funds may not publish any yet
    if not any(d.doc_type == "kid" for d in f.docs):
        issues.append("no KID")
    if not any(d.doc_type == "factsheet" for d in f.docs):
        issues.append("no factsheet")
    return issues


def _older_than_years(f: Fund, years: int) -> bool:
    if not f.inception:
        return True
    try:
        start = date.fromisoformat(f.inception.v)
    except ValueError:
        return True
    return (date.today() - start).days > 365 * years
