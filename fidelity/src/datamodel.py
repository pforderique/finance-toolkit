"""Data models for Fidelity holdings and sheet rows."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class HoldingRecord:
    """Normalized representation of a holding from the Fidelity CSV."""

    ticker: str
    account_label: str
    shares: float
    avg_cost: float
    description: Optional[str]


@dataclass(frozen=True)
class SheetRow:
    """Representation of a row pulled from Google Sheets."""

    ticker: str
    account_label: str
    shares: Optional[float]
    avg_cost: Optional[float]
    description: Optional[str]
    raw_index: int


@dataclass
class TableState:
    """Container for the existing sheet state and relevant metadata."""

    rows: List[SheetRow]
    start_row_index: int
    raw_values: List[List[str]]
    value_rows: List[List[str]]
