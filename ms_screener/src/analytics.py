"""Analytics functions for financial data processing."""

from datetime import date
from typing import List, Optional

from ms_screener.src import datamodel

InColumn = datamodel.InColumn
OutColumn = datamodel.OutColumn

SNAPSHOT_HEADERS = [
    OutColumn.COMPANY,
    OutColumn.TICKER,
    OutColumn.PRICE_CHANGE,
    OutColumn.LAST_PRICE,
    OutColumn.FAIR_VALUE,
    OutColumn.DISCOUNT,
    OutColumn.UNCERTAINTY,
    OutColumn.MOAT,
    OutColumn.STARS,
    OutColumn.RATINGS_DATE,
    OutColumn.LAST_SCRAPED,
    # OutColumn.IS_QUANT,
]

PERF_ID_KEY = "_perf_id"


def discount(price: Optional[float], fair_value: Optional[float]) -> Optional[float]:
    """Calculate the discount to fair value as a decimal
    (e.g., 0.25 for 25% undervalued). Return None if inputs are invalid.
    """
    # Imported lazily: transform imports analytics, so a module-level import here
    # makes `import ms_screener.src.transform` fail with a circular-import error.
    from ms_screener.src.transform import round_to

    if price is None or fair_value in (None, 0):
        return None
    try:
        return round_to(price / fair_value, 2)
    except ValueError:
        return None


def build_snapshot(full_rows: List[dict]) -> List[dict]:
    """Build a snapshot from normalized rows."""
    snapshot: List[dict] = []
    for row in full_rows:
        price = row.get(OutColumn.LAST_PRICE)
        payload = {
            OutColumn.COMPANY: row.get(OutColumn.COMPANY),
            OutColumn.TICKER: row.get(OutColumn.TICKER),
            OutColumn.PRICE_CHANGE: row.get(OutColumn.PRICE_CHANGE),
            OutColumn.LAST_PRICE: price,
            OutColumn.FAIR_VALUE: row.get(OutColumn.FAIR_VALUE),
            OutColumn.DISCOUNT: discount(price, row.get(OutColumn.FAIR_VALUE)),
            OutColumn.UNCERTAINTY: row.get(OutColumn.UNCERTAINTY),
            OutColumn.MOAT: row.get(OutColumn.MOAT),
            OutColumn.STARS: row.get(OutColumn.STARS),
            OutColumn.RATINGS_DATE: row.get(OutColumn.RATINGS_DATE),
            # OutColumn.IS_QUANT: row.get(OutColumn.IS_QUANT),
            OutColumn.LAST_SCRAPED: date.today().isoformat()
        }
        payload[PERF_ID_KEY] = row.get(InColumn.PERFORMANCE_ID)
        snapshot.append(payload)

    return snapshot
