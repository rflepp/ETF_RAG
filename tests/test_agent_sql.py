"""The read-only SQL screen: model-written queries may read the two fund tables and nothing else."""
import pytest

from agent.sql import FundsDB, SQLError
from agent_fakes import FUNDS


@pytest.fixture(scope="module")
def db():
    return FundsDB(FUNDS)


def test_screen_with_join_and_order(db):
    cols, rows, truncated = db.query("SELECT f.isin, ter, six_ccy FROM funds f JOIN fund_tags t USING (isin) "
                                     "WHERE t.tag = 'equity' AND hedged = 0 ORDER BY ter")
    assert cols == ["isin", "ter", "six_ccy"] and not truncated
    assert rows[0] == ("CH0237935652", 0.1, "CHF")
    assert len(rows) == 3


def test_rows_are_capped(db):
    _, rows, truncated = db.query("SELECT isin FROM funds", max_rows=2)
    assert len(rows) == 2 and truncated


@pytest.mark.parametrize("sql", [
    "DELETE FROM funds",
    "UPDATE funds SET ter = 0",
    "INSERT INTO fund_tags VALUES ('x', 'y')",
    "DROP TABLE funds",
    "SELECT 1; DROP TABLE funds",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA table_info(funds)",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('x')",
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c",
])
def test_everything_else_is_rejected(db, sql):
    with pytest.raises(SQLError):
        db.query(sql)
    assert db.query("SELECT count(*) FROM funds")[1] == [(3,)]


def test_runaway_query_is_interrupted():
    big = FundsDB([{**FUNDS[0], "isin": f"X{i}"} for i in range(200)])
    with pytest.raises(SQLError, match="interrupted"):
        big.query("SELECT count(*) FROM funds a, funds b, funds c")
