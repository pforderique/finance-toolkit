"""Data models for the screener application."""

from enum import Enum

# Edits allowed, but must match collected_data tab of the Google Sheet.
class InColumn(str, Enum):
    """Columns in the collected data Google Sheet."""
    TICKER = "Ticker"
    PERFORMANCE_ID = "Performance_ID"
    UNCERTAINTY = "Uncertainty"
    RATINGS_DATE = "Ratings_Date"


# No Edits.
class MSColumn(str, Enum):
    """Columns in the Morningstar CSV export."""
    COMPANY = "Name"
    TICKER = "Ticker"
    LAST_PRICE = "Last Price"
    PRICE_CHANGE = "Change (%)"
    # SECTOR = "Sector"
    # INDUSTRY = "Industry"
    MOAT = "Economic Moat"
    RATING = "Morningstar Rating for Stocks"
    FAIR_VALUE = "Fair Value"


# Edits allowed.
class OutColumn(str, Enum):
    """Columns in the output snapshot and fair value delta sheets."""
    COMPANY = "company"
    TICKER = "ticker"
    PRICE_CHANGE = "price_change (%)"
    LAST_PRICE = "last_price"
    FAIR_VALUE = "fair_value"
    MOAT = "moat"
    STARS = "stars"
    DISCOUNT = "discount"
    LAST_SCRAPED = "last_scraped"
    IS_QUANT = "is_quant"
    SOURCE_FILE = "source_file"

    def computed(self) -> bool:
        """Whether this column is computed (not directly from input)."""
        return self in {OutColumn.DISCOUNT, OutColumn.SOURCE_FILE}
