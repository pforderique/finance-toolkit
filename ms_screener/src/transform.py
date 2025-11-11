"""Data transformation utilities for the Morningstar screener tool."""

import datetime as dt
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from ms_screener.src import datamodel
from ms_screener.src import io_layer


InColumn = datamodel.InColumn
MSColumn = datamodel.MSColumn
OutColumn = datamodel.OutColumn

PERF_ID_RE = re.compile(r"^0P[A-Z0-9]+$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOT_AVAILABLE = "N/A"
ERR = "ERR"

FMV_CHANGE_HEADERS = [
    OutColumn.TICKER,
    OutColumn.COMPANY,
    "previous_fair_value",
    "current_fair_value",
    "fair_value_delta",
    "previous_stars",
    "current_stars",
    "previous_rating_date",
    "current_rating_date",
]


def normalize_collected_data(rows: Iterable[dict]) -> Tuple[List[dict], List[str]]:
    """Normalize and validate collected data rows."""

    warnings: List[str] = []
    normalized: List[dict] = []
    seen_ticker = set()
    seen_perf = set()

    for row in rows:
        ticker = row.get(InColumn.TICKER, "").strip().upper()
        perf = row.get(InColumn.PERFORMANCE_ID, "").strip().upper()
        date_iso = coerce_date(row.get(InColumn.RATINGS_DATE, ""))
        uncertainty = row.get(InColumn.UNCERTAINTY, "").strip().title()

        if not ticker:
            warnings.append("Row with missing ticker skipped.")
            continue
        if not perf or not PERF_ID_RE.match(perf):
            warnings.append(
                f"{ticker}: invalid perf_id '{perf}' → skipped for compare URLs.")
        if ticker in seen_ticker:
            warnings.append(f"Duplicate ticker in Collected Data: {ticker}")
        if perf and perf in seen_perf:
            warnings.append(f"Duplicate perf_id in Collected Data: {perf}")

        seen_ticker.add(ticker)
        if perf:
            seen_perf.add(perf)

        normalized.append(
            {
                InColumn.TICKER: ticker,
                InColumn.PERFORMANCE_ID: perf,
                InColumn.RATINGS_DATE: date_iso,
                InColumn.UNCERTAINTY: uncertainty,
            }
        )

    return normalized, warnings


def coerce_date(value: str | None) -> Optional[str]:
    """Coerce a date string into ISO format (YYYY-MM-DD), or return None if invalid."""
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(trimmed, fmt).strftime("%b %d, %Y")
        except ValueError:
            continue
    if DATE_RE.match(trimmed):
        return trimmed
    return None


def strip_q(value: str | None) -> Tuple[Optional[str], bool]:
    """Strip 'Q' or ' q ' from a string, returning the cleaned string and a boolean flag."""
    if value is None:
        return None, False
    text = str(value).strip()
    if not text:
        return "", False
    is_quant = False
    if re.search(r"(?:^|\s)Q(?:$|\s)", text, flags=re.IGNORECASE) or text.upper().endswith("Q"):
        is_quant = True
        text = re.sub(r"(?:^|\s)Q(?:$|\s)", " ", text,
                      flags=re.IGNORECASE).strip()
        if text.upper().endswith("Q"):
            text = text[:-1].strip()
    return text, is_quant


def to_float(value: str | None) -> Optional[float]:
    """Convert a string to a float, handling common formatting issues."""
    if value is None:
        return None
    text = value.strip().replace(",", "").replace("$", "")
    if not text or text == "—":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: str | None) -> Optional[int]:
    """Convert a string to an integer, handling common formatting issues."""
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text or text == "—":
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        # pylint: disable=broad-except
        except Exception:
            return None


def round_to(value: Optional[float], digits: int) -> Optional[float]:
    """Round a float to a specified number of decimal places, or return None if input is None."""
    if value is None:
        return None
    return round(value, digits)


def coerce_float(value: Optional[object]) -> Optional[float]:
    """Attempt to coerce a value (string or numeric) into a float."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return to_float(value)
    return to_float(str(value))


def coerce_int(value: Optional[object]) -> Optional[int]:
    """Attempt to coerce a value (string or numeric) into an int."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return to_int(value)
    return to_int(str(value))


def parse_mstar_csv(path: Path) -> List[dict]:
    """Parse a raw Morningstar CSV file into normalized rows."""
    raw = io_layer.read_csv_any(path)
    # clean up column names
    for row in raw:
        for k, v in list(row.items()):
            row[k.strip()] = v

    results: List[dict] = []

    ms_company_col = "Name"
    ms_moat_col = "Economic Moat"
    ms_rating_col = "Morningstar Rating for Stocks"
    ms_fair_value_col = "Fair Value"

    # verify required columns
    required = (
        MSColumn.TICKER, MSColumn.LAST_PRICE, MSColumn.PRICE_CHANGE,
        MSColumn.MOAT, MSColumn.RATING, MSColumn.FAIR_VALUE,
    )
    for idx, row in enumerate(raw):
        for req_col in required:
            if req_col in row:
                continue
            raise ValueError(
                f"Missing required column '{req_col}' in {path} at row {idx + 1}")

    def get_case_insensitive(row: dict, key: str) -> str:
        for candidate in row.keys():
            if candidate.lower() == key.lower():
                return row[candidate]
        return ERR

    for row in raw:
        ticker = get_case_insensitive(row, MSColumn.TICKER).strip().upper()
        last_price = to_float(get_case_insensitive(row, MSColumn.LAST_PRICE))
        price_change = to_float(
            get_case_insensitive(row, MSColumn.PRICE_CHANGE))
        # NOTE: while price_change is denoted as a percentage, Morningstar
        # actually exports the marginal dollar change, not the percentage.
        yesterday_price = last_price - price_change if last_price and price_change else None
        price_change_percent = round_to(
            (last_price / yesterday_price - 1)
            if last_price and yesterday_price and yesterday_price != 0 else None,
            4
        )
        stars_raw, stars_quant = strip_q(
            get_case_insensitive(row, ms_rating_col))
        fv_raw, fv_quant = strip_q(
            get_case_insensitive(row, ms_fair_value_col))
        moat_raw, moat_quant = strip_q(get_case_insensitive(row, ms_moat_col))

        stars = to_int(stars_raw)
        fair_value = to_float(fv_raw)
        moat = (moat_raw or "").strip().title()

        results.append(
            {
                OutColumn.COMPANY: get_case_insensitive(row, ms_company_col).strip(),
                OutColumn.TICKER: ticker,
                OutColumn.PRICE_CHANGE: price_change_percent,
                OutColumn.LAST_PRICE: last_price,
                OutColumn.STARS: stars,
                OutColumn.FAIR_VALUE: fair_value,
                OutColumn.MOAT: moat,
                OutColumn.IS_QUANT: bool(stars_quant or fv_quant or moat_quant),
                OutColumn.SOURCE_FILE: str(path),
            }
        )

    return results


def merge_dedupe(rows: List[dict]) -> List[dict]:
    """Merge and deduplicate rows based on ticker, preferring latest ratings date and file mtime."""
    by_ticker: dict[str, dict] = {}
    file_mtime: dict[str, float] = {}

    for row in rows:
        ticker = row.get(OutColumn.TICKER) or ""
        if not ticker:
            print("Warning: Skipping row with missing ticker during dedupe.")
            continue

        source_file = row.get(OutColumn.SOURCE_FILE)
        mtime = (os.path.getmtime(source_file)
                 if source_file and os.path.exists(source_file) else 0.0)
        file_mtime[ticker] = max(file_mtime.get(ticker, 0.0), mtime)

        prior = by_ticker.get(ticker)
        if prior is None:
            by_ticker[ticker] = row
            continue

        new_date = row.get(InColumn.RATINGS_DATE)
        old_date = prior.get(InColumn.RATINGS_DATE)
        if new_date and old_date:
            if new_date > old_date:
                by_ticker[ticker] = row
            elif new_date == old_date and mtime >= file_mtime.get(ticker, 0.0):
                by_ticker[ticker] = row
        elif new_date and not old_date:
            by_ticker[ticker] = row
        elif not new_date and not old_date and mtime >= file_mtime.get(ticker, 0.0):
            by_ticker[ticker] = row

    return list(by_ticker.values())


def merge_with_collected_data(data_rows: List[dict], collected_rows: List[dict]) -> List[dict]:
    """Merge normalized data rows with collected data based on ticker."""
    collected_map = {
        row[InColumn.TICKER]: row for row in collected_rows if row.get(InColumn.TICKER)}
    merged: List[dict] = []

    for row in data_rows:
        ticker = row.get(OutColumn.TICKER)
        if not ticker:
            print(
                "Warning: Skipping row with missing ticker during merge with collected data.")
            continue
        collected = collected_map.get(ticker, {})
        collected_clean = {k: v for k,
                           v in collected.items() if k != InColumn.TICKER}
        merged_row = {**row, **collected_clean}
        merged.append(merged_row)

    return merged


def diff_snapshots(prev_rows: List[dict], curr_rows: List[dict]) -> List[dict]:
    """Diff two snapshots and return rows with changes in key fields."""
    prior = {
        row[InColumn.PERFORMANCE_ID]: row for row in prev_rows
        if row.get(InColumn.PERFORMANCE_ID)
    }
    changes: List[dict] = []

    for row in curr_rows:
        perf = row.get(InColumn.PERFORMANCE_ID)
        if not perf or perf not in prior:
            continue
        baseline = prior[perf]
        fields = ("stars", "fair_value", "moat", InColumn.RATINGS_DATE)
        deltas = {field: (baseline.get(field), row.get(field))
                  for field in fields if baseline.get(field) != row.get(field)}
        if deltas:
            changes.append({InColumn.PERFORMANCE_ID: perf,
                           InColumn.TICKER: row.get(InColumn.TICKER), **deltas})

    return changes


def compare_ready_perf_ids(collected_rows: Iterable[dict]) -> List[str]:
    """Extract valid performance IDs from collected data for comparison links."""
    perf_ids = []
    for row in collected_rows:
        perf = row.get(InColumn.PERFORMANCE_ID) or ""
        if perf and PERF_ID_RE.match(perf):
            perf_ids.append(perf)
    return sorted(set(perf_ids))


def detect_fmv_changes(prev_rows: Iterable[dict], curr_rows: Iterable[dict]) -> List[dict]:
    """Identify tickers where fair value changed between previous and current snapshots."""

    prev_by_ticker: dict[str, dict] = {}
    for row in prev_rows:
        raw_ticker = row.get(OutColumn.TICKER) or row.get(
            OutColumn.TICKER.title()) or ""
        ticker = raw_ticker.strip().upper()
        if ticker:
            prev_by_ticker[ticker] = row

    changes: List[dict] = []
    for row in curr_rows:
        ticker_value = row.get(OutColumn.TICKER) or ""
        ticker = ticker_value.strip().upper()
        if not ticker or ticker not in prev_by_ticker:
            continue

        prev_row = prev_by_ticker[ticker]
        prev_fv = coerce_float(
            prev_row.get(OutColumn.FAIR_VALUE) or prev_row.get(
                OutColumn.FAIR_VALUE.title())
        )
        curr_fv = coerce_float(row.get(OutColumn.FAIR_VALUE))

        if prev_fv is None and curr_fv is None:
            continue
        if prev_fv is not None and curr_fv is not None and abs(prev_fv - curr_fv) < 1e-6:
            continue

        delta = None
        if prev_fv is not None and curr_fv is not None:
            delta = round_to(curr_fv - prev_fv, 2)

        change_row = {
            OutColumn.TICKER: row.get(OutColumn.TICKER) or prev_row.get(OutColumn.TICKER),
            OutColumn.COMPANY: row.get(OutColumn.COMPANY) or prev_row.get(OutColumn.COMPANY),
            "previous_fair_value": round_to(prev_fv, 2),
            "current_fair_value": round_to(curr_fv, 2),
            "fair_value_delta": delta,
            "previous_stars": coerce_int(
                prev_row.get(OutColumn.STARS) or prev_row.get(
                    OutColumn.STARS.title())
            ),
            "current_stars": coerce_int(row.get(OutColumn.STARS)),
            "previous_rating_date": (
                prev_row.get(InColumn.RATINGS_DATE) or prev_row.get(
                    InColumn.RATINGS_DATE.title())
            ),
            "current_rating_date": row.get(InColumn.RATINGS_DATE),
        }
        changes.append(change_row)

    return changes
