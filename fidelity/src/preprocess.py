"""Preprocessing logic for Fidelity CSV inputs.

Header matching is case/whitespace-insensitive with a small compat-alias table so
older exports (which use Title Case headers like "Account Name") and newer exports
(sentence case, e.g. "Account name") both parse without code changes.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

from fidelity.src.datamodel import HoldingKey, HoldingRecord
from fidelity.src.settings import Settings

_WS_RE = re.compile(r"\s+")


class PreprocessError(Exception):
    """Raised when the input CSV cannot be processed."""


def _normalize_header(header: str) -> str:
    return _WS_RE.sub(" ", header.strip().lower())


# Canonical (normalized) header name -> set of normalized variants that mean the same
# thing. Extend this if a future export renames a column again.
_HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "account number": ("account number", "acct number", "account #"),
    "account name": ("account name", "acct name"),
    "symbol": ("symbol", "ticker"),
    "description": ("description",),
    "quantity": ("quantity", "qty"),
    "average cost basis": ("average cost basis", "avg cost basis", "avg. cost basis"),
    "cost basis total": ("cost basis total", "total cost basis"),
}

_REQUIRED_CANONICAL = {
    "account number",
    "account name",
    "symbol",
    "quantity",
    "average cost basis",
}

_VARIANT_TO_CANONICAL: Dict[str, str] = {
    variant: canonical
    for canonical, variants in _HEADER_ALIASES.items()
    for variant in variants
}


def _build_header_map(raw_row: Dict[str, str]) -> Dict[str, str]:
    """Map canonical field name -> the actual header string used in this CSV."""
    header_map: Dict[str, str] = {}
    for header in raw_row.keys():
        if header is None:
            continue  # DictReader yields a None key for the trailing "," on each row
        normalized = _normalize_header(header)
        canonical = _VARIANT_TO_CANONICAL.get(normalized, normalized)
        if canonical not in header_map:
            header_map[canonical] = header
    return header_map


def _get(raw: Dict[str, str], header_map: Dict[str, str], canonical: str) -> str:
    header = header_map.get(canonical)
    if header is None:
        return ""
    value = raw.get(header)
    return value.strip() if isinstance(value, str) else (str(value).strip() if value is not None else "")


def _parse_float(value: str) -> Optional[float]:
    cleaned = value.replace(",", "").replace("$", "").strip()
    if cleaned == "":
        return None
    return float(cleaned)


def preprocess_rows(
    raw_rows: List[Dict[str, str]], settings: Settings
) -> Tuple[List[HoldingRecord], Set[HoldingKey], Set[str], List[str]]:
    """Normalize Fidelity CSV rows into holdings.

    Returns (holdings, skipped_keys, observed_labels, warnings):
    - holdings: aggregated HoldingRecord per (ticker, account_label).
    - skipped_keys: (ticker, account_label) pairs that were SEEN in the CSV for an
      owned/enabled account but could not be parsed (bad numbers). This is the
      protected set the diff engine must NOT delete -- an unparsable row must never
      look identical to "this holding no longer exists".
    - observed_labels: account labels actually seen in this CSV (for any row that
      reached account resolution, whether or not its numbers parsed). Scopes the
      diff engine's delete set to accounts actually present in this export, so a
      partial export never mass-deletes accounts it didn't mention.
    - warnings: human-readable, aggregated (not per-row) where the plan requires it.

    Filter order matters and is preserved from the legacy implementation:
    empty symbol -> skip silent; ignore_prefixes/ignore_exact -> skip silent;
    THEN account resolution; THEN numbers.
    """

    if not raw_rows:
        raise PreprocessError("No raw rows to process")

    header_map = _build_header_map(raw_rows[0])
    missing = _REQUIRED_CANONICAL - set(header_map)
    if missing:
        found_headers = sorted(h for h in raw_rows[0].keys() if h)
        raise PreprocessError(
            f"Input CSV is missing required columns: {', '.join(sorted(missing))}. "
            f"Headers found: {found_headers}"
        )

    warnings: List[str] = []
    skipped_keys: Set[HoldingKey] = set()
    observed_labels: Set[str] = set()
    unmapped_counts: "OrderedDict[Tuple[str, str], int]" = OrderedDict()

    ignore_exact = {s.strip().upper() for s in settings.symbols.ignore_exact}
    ignore_prefixes = tuple(p.strip().upper() for p in settings.symbols.ignore_prefixes)
    aliases = {k.strip().upper(): v for k, v in settings.symbols.aliases.items()}

    # key -> list of (quantity, cost_total_or_None, avg_cost_or_None, description_or_None)
    groups: "OrderedDict[HoldingKey, List[Tuple[float, Optional[float], Optional[float], Optional[str]]]]" = (
        OrderedDict()
    )

    for raw in raw_rows:
        symbol_raw = _get(raw, header_map, "symbol")
        if not symbol_raw:
            # Common and expected: blank lines, quoted disclaimer rows with no symbol.
            continue

        symbol = symbol_raw.strip().upper()

        if symbol in ignore_exact or any(symbol.startswith(p) for p in ignore_prefixes):
            continue

        account_number = _get(raw, header_map, "account number")
        account_name = _get(raw, header_map, "account name")
        label = settings.resolve_label(account_number, account_name)

        if label is None:
            if account_number or account_name:
                unmapped_key = (account_number, account_name)
                unmapped_counts[unmapped_key] = unmapped_counts.get(unmapped_key, 0) + 1
            continue

        observed_labels.add(label)

        # Apply symbol aliases AFTER upper-casing (e.g. BRKB -> BRK.B), but pass
        # CUSIP-style symbols (401k funds) through verbatim -- they simply have no
        # alias entry so this is a no-op for them.
        symbol = aliases.get(symbol, symbol)
        key: HoldingKey = (symbol, label)

        quantity_raw = _get(raw, header_map, "quantity")
        try:
            quantity = _parse_float(quantity_raw)
        except ValueError:
            skipped_keys.add(key)
            warnings.append(
                f"Skipping row for {symbol}/{label}: invalid quantity '{quantity_raw}'"
            )
            continue
        if quantity is None:
            skipped_keys.add(key)
            warnings.append(f"Skipping row for {symbol}/{label}: blank quantity")
            continue

        cost_total_raw = _get(raw, header_map, "cost basis total")
        try:
            cost_total = _parse_float(cost_total_raw)
        except ValueError:
            cost_total = None

        avg_cost_raw = _get(raw, header_map, "average cost basis")
        try:
            avg_cost = _parse_float(avg_cost_raw)
        except ValueError:
            avg_cost = None

        if cost_total is None and avg_cost is None:
            skipped_keys.add(key)
            warnings.append(
                f"Skipping row for {symbol}/{label}: no usable cost basis "
                f"(cost basis total='{cost_total_raw}', average cost basis='{avg_cost_raw}')"
            )
            continue

        description = _get(raw, header_map, "description") or None
        groups.setdefault(key, []).append((quantity, cost_total, avg_cost, description))

    for (account_number, account_name), count in unmapped_counts.items():
        warnings.append(
            f"Account '{account_name}' (number '{account_number}') is not mapped to an "
            f"enabled sheet label; skipped {count} row(s). Use `fidelity accounts add/edit` "
            "to map it."
        )

    holdings: List[HoldingRecord] = []
    for (symbol, label), entries in groups.items():
        total_shares = sum(q for q, _, _, _ in entries)
        if total_shares == 0:
            skipped_keys.add((symbol, label))
            warnings.append(
                f"Skipping {symbol}/{label}: aggregated quantity is zero across "
                f"{len(entries)} row(s)"
            )
            continue

        if len(entries) == 1:
            # Single CSV row for this key: use Fidelity's own Average Cost Basis
            # verbatim. Deriving it as cost_total/shares instead reintroduces
            # precision Fidelity already rounded away (2dp), producing phantom
            # updates against the sheet's stored 2dp value. The
            # sum(cost_total)/sum(shares) blend is only correct -- and only
            # needed -- when 2+ rows are actually being combined into one key.
            q, cost_total, avg_cost_val, _ = entries[0]
            if avg_cost_val is not None:
                avg_cost = avg_cost_val
            else:
                avg_cost = cost_total / q
        elif all(cost_total is not None for _, cost_total, _, _ in entries):
            total_cost = sum(cost_total for _, cost_total, _, _ in entries)
            avg_cost = total_cost / total_shares
        else:
            # Fall back to a share-weighted mean of Average Cost Basis. For any row
            # in the group missing that too, derive an effective per-row avg cost
            # from its own cost_total/quantity (already guaranteed to have at least
            # one of the two above).
            weighted_sum = 0.0
            for q, cost_total, avg_cost_val, _ in entries:
                effective = avg_cost_val if avg_cost_val is not None else cost_total / q
                weighted_sum += q * effective
            avg_cost = weighted_sum / total_shares

        if len(entries) > 1:
            warnings.append(
                f"Aggregated {len(entries)} CSV rows for {symbol}/{label} into one holding "
                f"(shares={total_shares}, avg_cost={avg_cost:.4f})"
            )

        description = next((d for _, _, _, d in entries if d), None)
        holdings.append(
            HoldingRecord(
                ticker=symbol,
                account_label=label,
                shares=total_shares,
                avg_cost=avg_cost,
                description=description,
            )
        )

    return holdings, skipped_keys, observed_labels, warnings
