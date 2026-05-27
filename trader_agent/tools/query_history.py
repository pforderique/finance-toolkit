"""Query FMV and stars history for one or more tickers from the local SQLite DB.

Usage:
    python -m trader_agent.tools.query_history MSFT AAPL NKE

Reads /tmp/fmv_history.db (built by history_db.py) and prints a JSON dict
keyed by ticker. Each value contains the revision history and derived trends.

Output schema per ticker:
{
  "ticker": "MSFT",
  "revisions": 3,
  "fmv_history": [
    {"date": "2025-01-10", "previous_fmv": 420.0, "current_fmv": 460.0,
     "delta": 40.0, "previous_stars": 4, "current_stars": 5}
  ],
  "net_fmv_change_pct": 9.5,
  "fmv_direction": "up",
  "stars_start": 4,
  "stars_end": 5,
  "stars_direction": "rising",
  "downgrades": 0
}
"""

import json
import sqlite3
import sys

DB_PATH = "/tmp/fmv_history.db"


def _direction(start: float | None, end: float | None) -> str:
    if start is None or end is None:
        return "unknown"
    if end > start * 1.01:
        return "up"
    if end < start * 0.99:
        return "down"
    return "flat"


def _stars_direction(start: int | None, end: int | None) -> str:
    if start is None or end is None:
        return "unknown"
    if end > start:
        return "rising"
    if end < start:
        return "falling"
    return "stable"


def query_ticker(cur: sqlite3.Cursor, ticker: str) -> dict:
    rows = cur.execute(
        """
        SELECT date, previous_fair_value, current_fair_value, fair_value_delta,
               previous_stars, current_stars
        FROM fmv_history
        WHERE ticker = ?
        ORDER BY date ASC
        """,
        (ticker.upper(),),
    ).fetchall()

    if not rows:
        return {"ticker": ticker, "revisions": 0, "fmv_history": [], "error": "no data"}

    history = [
        {
            "date": r[0],
            "previous_fmv": r[1],
            "current_fmv": r[2],
            "delta": r[3],
            "previous_stars": r[4],
            "current_stars": r[5],
        }
        for r in rows
    ]

    first_fmv = next((r["previous_fmv"] for r in history if r["previous_fmv"] is not None), None)
    last_fmv = next((r["current_fmv"] for r in reversed(history) if r["current_fmv"] is not None), None)
    net_pct = round((last_fmv - first_fmv) / first_fmv * 100, 1) if first_fmv and last_fmv and first_fmv != 0 else None

    downgrades = sum(1 for r in history if r["delta"] is not None and r["delta"] < 0)

    stars_start = next((r["previous_stars"] for r in history if r["previous_stars"] is not None), None)
    stars_end = next((r["current_stars"] for r in reversed(history) if r["current_stars"] is not None), None)

    return {
        "ticker": ticker,
        "revisions": len(rows),
        "fmv_history": history,
        "net_fmv_change_pct": net_pct,
        "fmv_direction": _direction(first_fmv, last_fmv),
        "stars_start": stars_start,
        "stars_end": stars_end,
        "stars_direction": _stars_direction(stars_start, stars_end),
        "downgrades": downgrades,
    }


def query_tickers(tickers: list[str], db_path: str = DB_PATH) -> dict:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {"error": f"DB not found at {db_path} — run: python -m trader_agent.tools.history_db"}

    cur = con.cursor()
    results = {t: query_ticker(cur, t) for t in tickers}
    con.close()
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m trader_agent.tools.query_history TICKER [TICKER ...]', file=sys.stderr)
        sys.exit(1)

    tickers = [t.upper() for t in sys.argv[1:]]
    results = query_tickers(tickers)
    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
