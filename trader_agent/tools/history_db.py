"""Load FMV_History from Google Sheets into a local SQLite DB at /tmp/fmv_history.db.

Run once at the start of the trader_agent workflow:
    python -m trader_agent.tools.history_db

Prints a JSON summary: {"rows": N, "tickers": N, "date_range": ["YYYY-MM-DD", "YYYY-MM-DD"]}
"""

import json
import os
import sqlite3
import sys

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

DB_PATH = "/tmp/fmv_history.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS fmv_history (
    date                 TEXT,
    ticker               TEXT,
    company              TEXT,
    previous_fair_value  REAL,
    current_fair_value   REAL,
    fair_value_delta     REAL,
    previous_stars       INTEGER,
    current_stars        INTEGER,
    previous_rating_date TEXT,
    current_rating_date  TEXT
)
"""


def _safe_float(val) -> float | None:
    try:
        return float(str(val).strip().replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def build_db(sheet_id: str, db_path: str = DB_PATH) -> dict:
    from trader_agent.tools.loader import load_fmv_history

    rows = load_fmv_history(sheet_id)

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS fmv_history")
    cur.execute(CREATE_TABLE)

    inserted = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO fmv_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (row.get("date") or "").strip() or None,
                (row.get("ticker") or "").strip().upper() or None,
                (row.get("company") or "").strip() or None,
                _safe_float(row.get("previous_fair_value")),
                _safe_float(row.get("current_fair_value")),
                _safe_float(row.get("fair_value_delta")),
                _safe_int(row.get("previous_stars")),
                _safe_int(row.get("current_stars")),
                (row.get("previous_rating_date") or "").strip() or None,
                (row.get("current_rating_date") or "").strip() or None,
            ),
        )
        inserted += 1

    con.commit()

    dates = [r[0] for r in cur.execute("SELECT date FROM fmv_history WHERE date IS NOT NULL ORDER BY date").fetchall()]
    tickers = cur.execute("SELECT COUNT(DISTINCT ticker) FROM fmv_history").fetchone()[0]
    con.close()

    return {
        "rows": inserted,
        "tickers": tickers,
        "date_range": [dates[0], dates[-1]] if dates else [],
        "db_path": db_path,
    }


if __name__ == "__main__":
    sheet_id = os.environ["SHEET_ID"]
    summary = build_db(sheet_id)
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
