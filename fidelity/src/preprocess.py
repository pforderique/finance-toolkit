"""Preprocessing logic for Fidelity CSV inputs."""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, List, Tuple

from fidelity.src import constants
from fidelity.src.datamodel import HoldingRecord


class PreprocessError(Exception):
    """Raised when the input CSV cannot be processed."""


def _normalize_symbol(symbol: str) -> str:
    # Handle BRK.B special case since Google Sheets uses the dot notation
    if symbol == "BRKB":
        symbol = "BRK.B"
    return symbol.strip().upper()


def _parse_float(value: str) -> float:
    cleaned = value.replace(",", "").replace("$", "").strip()
    if cleaned == "":
        raise ValueError("empty value")
    return float(cleaned)


def preprocess_rows(raw_rows: List[dict[str, str]]) -> Tuple[List[HoldingRecord], List[str]]:
    """Normalize Fidelity CSV rows into holdings and collect warnings."""

    if not raw_rows:
        raise PreprocessError("No raw rows to process")

    missing_columns = [
        col for col in constants.REQUIRED_CSV_COLUMNS 
        if col not in raw_rows[0]
    ]
    if missing_columns:
        raise PreprocessError(
            f"Input CSV is missing required columns: {', '.join(sorted(missing_columns))}"
        )

    warnings: List[str] = []
    normalized: Dict[Tuple[str, str], HoldingRecord] = OrderedDict()

    for raw in raw_rows:
        try:
            symbol = _normalize_symbol(raw.get(constants.CSV_SYMBOL, "") or "")
        except AttributeError:
            warnings.append(f"Skipping malformed row lacking a symbol: {raw}")
            continue

        if not symbol:
            # no need to warn here, as this is common in Fidelity exports
            continue

        if symbol in constants.OMIT_SYMBOL_VALUES or any(
            symbol.startswith(prefix)
            for prefix in constants.OMIT_SYMBOL_PREFIXES
        ):
            continue

        account_name = raw.get(constants.CSV_ACCOUNT_NAME, "").strip()
        if not account_name:
            warnings.append(f"Skipping ticker {symbol} due to missing account name")
            continue

        account_label = constants.ACCOUNT_NAME_TO_SHEET_LABEL.get(account_name)
        if not account_label:
            warnings.append(
                f"Account '{account_name}' not recognized for ticker {symbol}; skipping"
            )
            continue

        quantity_raw = raw.get(constants.CSV_QUANTITY, "").strip()
        try:
            quantity = _parse_float(quantity_raw)
        except ValueError:
            warnings.append(
                f"Skipping ticker {symbol} for account {account_label} due to invalid quantity '{quantity_raw}'"
            )
            continue

        avg_cost_raw = raw.get(constants.CSV_AVG_COST, "").strip()
        try:
            avg_cost = _parse_float(avg_cost_raw)
        except ValueError:
            warnings.append(
                f"Skipping ticker {symbol} for account {account_label} due to invalid avg cost '{avg_cost_raw}'"
            )
            continue

        description = (raw.get(constants.CSV_DESCRIPTION) or "").strip() or None

        key = (symbol, account_label)
        record = HoldingRecord(
            ticker=symbol,
            account_label=account_label,
            shares=quantity,
            avg_cost=avg_cost,
            description=description,
        )

        if key in normalized:
            warnings.append(
                f"Duplicate holding detected for {symbol} / {account_label}; keeping the latest values"
            )
        normalized[key] = record

    return list(normalized.values()), warnings
