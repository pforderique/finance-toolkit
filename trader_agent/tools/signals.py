"""FMV upgrade detection and staleness flags."""

import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta

from trader_agent.tools.scorer import ScoredStock


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def detect_fmv_upgrades(
    fmv_history: list[dict],
    lookback_days: int = 60,
    threshold_pct: float = 15.0,
) -> dict[str, float]:
    cutoff = date.today() - timedelta(days=lookback_days)

    # group by ticker, filter to window
    by_ticker: dict[str, list[tuple[date, float, float]]] = {}
    for row in fmv_history:
        ticker = str(row.get("ticker", "")).strip()
        d = _parse_date(row.get("date"))
        if d is None or d < cutoff:
            continue
        old_fmv = _safe_float(row.get("old_fmv"))
        new_fmv = _safe_float(row.get("new_fmv"))
        if old_fmv is None or new_fmv is None:
            continue
        by_ticker.setdefault(ticker, []).append((d, old_fmv, new_fmv))

    result = {}
    for ticker, entries in by_ticker.items():
        entries.sort(key=lambda x: x[0])
        oldest_old_fmv = entries[0][1]   # old_fmv of earliest entry
        newest_new_fmv = entries[-1][2]  # new_fmv of latest entry
        if oldest_old_fmv == 0:
            continue
        change_pct = (newest_new_fmv - oldest_old_fmv) / oldest_old_fmv * 100
        if change_pct > threshold_pct:
            result[ticker] = change_pct

    return result


def _safe_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def flag_stale(scored: list[ScoredStock]) -> list[ScoredStock]:
    for s in scored:
        if s.ratings_age_days is None or s.ratings_age_days > 180:
            s.stale_rating = True
    return scored


def apply_fmv_flags(
    scored: list[ScoredStock],
    upgrades: dict[str, float],
) -> list[ScoredStock]:
    for s in scored:
        if s.ticker in upgrades:
            s.fmv_upgraded = True
    return scored


if __name__ == "__main__":
    from trader_agent.tools.loader import load_fmv_history

    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    history = load_fmv_history(sheet_id)
    upgrades = detect_fmv_upgrades(history)
    output = {ticker: {"fmv_upgraded": True, "change_pct": pct} for ticker, pct in upgrades.items()}
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")
