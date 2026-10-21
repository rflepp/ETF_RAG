"""Read-only SQL over the fund facts, for screening and ranking questions that vector search cannot answer
("accumulating world ETFs under 0.2% TER with more than 1bn in assets, sorted by 5y tracking difference").

The database is an in-memory SQLite copy of the fund facts held in Qdrant, built once per process. Queries come
from the model, so the connection only allows SELECT: an authorizer rejects every other action (writes, ATTACH,
PRAGMA, ...), a progress handler aborts runaway queries, and results are capped.
"""
from __future__ import annotations

import sqlite3
import threading

from . import domain

MAX_ROWS = 30
MAX_STEPS = 2_000_000  # SQLite VM instructions per query (progress handler is called every 1000)

SCHEMA = """CREATE TABLE funds (
  isin TEXT PRIMARY KEY, name TEXT, provider TEXT,
  asset_class TEXT,           -- 'Equity' | 'Fixed Income' | 'Multi Asset' | 'Real Estate' | ...
  sub_asset_class TEXT,       -- 'Large/Mid Cap' | 'All Cap' | 'Small Cap' | 'Credit' | 'Government' | 'High Yield' | ...
  region TEXT,                -- 'Global' | 'North America' | 'Europe' | 'Asia Pacific' | ... (NULL for some providers;
                              --  fund_tags is complete)
  market_type TEXT,           -- 'Developed' | 'Emerging'  (developed world = region 'Global' AND 'Developed')
  domicile TEXT,              -- 'Ireland' | 'Switzerland' | 'Germany' | ...
  replication TEXT,           -- 'physical-full' | 'physical-sampling' | 'synthetic'
  use_of_income TEXT,         -- 'Accumulating' | 'Distributing'
  hedged INTEGER,             -- 0 | 1
  hedge_currency TEXT, share_class_currency TEXT, benchmark TEXT,
  inception TEXT,             -- 'YYYY-MM-DD'
  ter REAL,                   -- percent per year, e.g. 0.20
  kid_ongoing_pct REAL, kid_transaction_pct REAL,
  sri INTEGER,                -- PRIIPs summary risk indicator 1..7
  fund_size_m REAL, fund_size_ccy TEXT,   -- millions in fund_size_ccy
  holdings_count INTEGER,
  td_avg_3y REAL, td_avg_5y REAL,         -- tracking difference p.a. in percent (fund minus benchmark)
  six_ccy TEXT                -- trading currency on SIX Swiss Exchange (NULL = not listed on SIX)
);
CREATE TABLE fund_tags (isin TEXT, tag TEXT);
  -- tags: 'world' (developed world), 'all-world' (incl. emerging), 'usa', 'europe', 'switzerland', 'japan',
  -- 'emerging-markets', 'esg', 'small-cap', 'semiconductors', 'healthcare', 'corporate-bonds', 'government-bonds',
  -- 'chf-hedged', 'currency-hedged', ...  Filter with: isin IN (SELECT isin FROM fund_tags WHERE tag = '...')
"""
COLUMNS = ["isin", "name", "provider", "asset_class", "sub_asset_class", "region", "market_type", "domicile", "replication", "use_of_income", "hedged",
           "hedge_currency", "share_class_currency", "benchmark", "inception", "ter", "kid_ongoing_pct",
           "kid_transaction_pct", "sri", "fund_size_m", "fund_size_ccy", "holdings_count", "td_avg_3y", "td_avg_5y",
           "six_ccy"]
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}
_TABLES = {"funds", "fund_tags"}


class SQLError(ValueError):
    pass


def _row(f: dict) -> tuple:
    v = lambda k: domain._v(f, k)  # noqa: E731
    six = domain.six_line(f)
    num = lambda x: None if x is None else float(x)  # noqa: E731
    row = {"isin": f["isin"], "name": v("name"), "provider": f.get("provider"), "hedged": int(bool(v("hedged"))),
           "fund_size_ccy": f.get("fund_size_ccy"), "td_avg_3y": f.get("td_avg_3y"), "td_avg_5y": f.get("td_avg_5y"),
           "six_ccy": (six or {}).get("ccy")}
    for k in ("asset_class", "sub_asset_class", "region", "market_type", "domicile", "replication", "use_of_income", "hedge_currency",
              "share_class_currency", "benchmark", "inception"):
        row[k] = v(k)
    for k in ("ter", "kid_ongoing_pct", "kid_transaction_pct", "fund_size_m"):
        row[k] = num(v(k))
    for k in ("sri", "holdings_count"):
        row[k] = None if v(k) is None else int(v(k))
    return tuple(row[c] for c in COLUMNS)


def _authorize(action: int, arg1, *_) -> int:
    ok = action in _ALLOWED or (action == sqlite3.SQLITE_READ and arg1 in _TABLES)
    return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY


class FundsDB:
    def __init__(self, funds: list[dict]):
        self._lock = threading.Lock()  # one connection shared by the API's worker threads
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.executemany(f"INSERT INTO funds VALUES ({','.join('?' * len(COLUMNS))})", [_row(f) for f in funds])
        self.conn.executemany("INSERT INTO fund_tags VALUES (?, ?)",
                              [(f["isin"], t) for f in funds for t in f.get("theme_tags", [])])
        self.conn.commit()
        self.conn.set_authorizer(_authorize)

    def query(self, sql: str, max_rows: int = MAX_ROWS) -> tuple[list[str], list[tuple], bool]:
        """Returns (columns, rows, truncated). Raises SQLError with a message the model can act on."""
        steps = 0

        def guard() -> int:
            nonlocal steps
            steps += 1000
            return 1 if steps > MAX_STEPS else 0

        with self._lock:
            self.conn.set_progress_handler(guard, 1000)
            try:
                cur = self.conn.execute(sql)
                rows = cur.fetchmany(max_rows + 1)
                cols = [d[0] for d in cur.description or []]
            except (sqlite3.DatabaseError, sqlite3.Warning) as e:  # includes "not authorized" and "interrupted"
                raise SQLError(str(e)) from e
            finally:
                self.conn.set_progress_handler(None, 0)
        return cols, rows[:max_rows], len(rows) > max_rows
