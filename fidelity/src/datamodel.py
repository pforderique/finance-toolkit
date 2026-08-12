"""Data models for Fidelity holdings, sheet rows, table metadata, and diff plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class HoldingRecord:
    """Normalized, aggregated representation of a holding from the Fidelity CSV."""

    ticker: str
    account_label: str
    shares: float
    avg_cost: float
    description: Optional[str] = None


@dataclass(frozen=True)
class SheetRow:
    """One data row read from the INVESTMENT_HOLDINGS table block."""

    ticker: str
    account_label: str
    shares: Optional[float]
    avg_cost: Optional[float]
    row_number: int  # absolute 1-based sheet row number


@dataclass(frozen=True)
class TargetRow:
    """One row of the materialized write target: the exact (ticker, shares,
    avg_cost, account_label) that will land in columns A/B/C/G for a given
    physical row of the block. Built by `workflow.build_target_block` and
    consumed unchanged by both the dry-run display and the real write --
    there is no separate "preview" representation."""

    ticker: str
    shares: Optional[float]
    avg_cost: Optional[float]
    account_label: str


@dataclass(frozen=True)
class TableInfo:
    """Resolved metadata for a native Sheets table, looked up by name (never by letter)."""

    sheet_id: int
    table_id: str
    table_name: str
    tab: str
    header_row: int
    first_data_row: int
    last_data_row: int
    capacity: int
    column_index_by_name: Dict[str, int]
    range_a1: str


@dataclass(frozen=True)
class ChangeEntry:
    """One row's worth of diff output: an add, update, delete, unchanged, or untouched row."""

    action: str  # "add" | "update" | "delete" | "unchanged" | "untouched"
    ticker: str
    account_label: str
    row_number: Optional[int]
    old_shares: Optional[float]
    old_avg_cost: Optional[float]
    new_shares: Optional[float]
    new_avg_cost: Optional[float]


def _equity(shares: Optional[float], avg_cost: Optional[float]) -> float:
    return (shares or 0.0) * (avg_cost or 0.0)


def equity_delta(entry: ChangeEntry) -> float:
    return _equity(entry.new_shares, entry.new_avg_cost) - _equity(
        entry.old_shares, entry.old_avg_cost
    )


@dataclass
class ChangePlan:
    """Output of the pure diff engine `workflow.plan_changes`. Zero I/O to produce."""

    adds: List[ChangeEntry] = field(default_factory=list)
    updates: List[ChangeEntry] = field(default_factory=list)
    deletes: List[ChangeEntry] = field(default_factory=list)
    unchanged: List[ChangeEntry] = field(default_factory=list)
    untouched: List[ChangeEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        return {
            "adds": len(self.adds),
            "updates": len(self.updates),
            "deletes": len(self.deletes),
            "unchanged": len(self.unchanged),
            "untouched": len(self.untouched),
        }

    def net_equity_delta(self) -> float:
        return sum(equity_delta(e) for e in self.adds + self.updates + self.deletes)

    def all_actionable(self) -> List[ChangeEntry]:
        """Adds + updates + deletes -- the rows that actually appear in the dry-run table."""
        return self.adds + self.updates + self.deletes


# Key type used throughout: (ticker, account_label)
HoldingKey = Tuple[str, str]
