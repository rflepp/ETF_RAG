"""Hand-checked values from real documents (factsheets as of 2026-09-30).

Runs against data/cache/pdf (filled by `python -m pipeline build`); documents are not committed,
so a missing PDF skips its case. When a provider publishes a new factsheet the monthly values
(returns, size) move; then re-check by hand and update the expectations here.
"""
import json
from pathlib import Path

import pytest

from pipeline.download import pdf_path
from pipeline.extract import ishares_factsheet, vanguard_factsheet
from pipeline.extract.kid import parse_kid
from pipeline.parse import read_pages

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text())
PARSERS = {"ishares": ishares_factsheet.parse_factsheet, "vanguard": vanguard_factsheet.parse_factsheet}


def _cases(kind):
    for isin, case in GOLDEN.items():
        for field, expected in case.get(kind, {}).items():
            yield pytest.param(isin, case["provider"], field, expected, id=f"{isin}-{kind}-{field}")


def _value(fact):
    v = fact.v
    if isinstance(v, list):
        return [x.model_dump() for x in v]
    return v


@pytest.mark.parametrize("isin,provider,field,expected", list(_cases("factsheet")))
def test_factsheet(isin, provider, field, expected):
    path = pdf_path(f"{isin}-factsheet")
    if not path.exists():
        pytest.skip("factsheet not downloaded")
    facts, _ = PARSERS[provider](read_pages(path), f"{isin}-factsheet")
    assert field in facts, f"{field} not extracted"
    got = _value(facts[field])
    if field == "calendar_returns":
        got = {str(r["year"]): [r["fund"], r["bench"]] for r in got}
        for year, pair in expected.items():
            assert got.get(year) == pair, year
    else:
        assert got == expected


@pytest.mark.parametrize("isin,provider,field,expected", list(_cases("kid")))
def test_kid(isin, provider, field, expected):
    path = pdf_path(f"{isin}-kid")
    if not path.exists():
        pytest.skip("KID not downloaded")
    facts = parse_kid(read_pages(path), f"{isin}-kid")
    assert field in facts, f"{field} not extracted"
    assert facts[field].v == expected
