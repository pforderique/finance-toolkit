"""Configuration dataclasses for the Fidelity portfolio sync tool."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fidelity.src import constants


@dataclass
class RunConfig:
    """Configuration for a single synchronization run."""

    csv_path: Path
    sheet_id: str
    tab_name: str = constants.PORTFOLIO_TAB
    dry_run: bool = False
    log_level: str = constants.DEFAULT_LOG_LEVEL
    artifacts_dir: Path = constants.ARTIFACTS_DIR
    timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def ensure_artifacts_dir(self) -> Path:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self.artifacts_dir


@dataclass
class ChangeRecord:
    """Represent a single modification made to Google Sheets."""

    action: str
    ticker: str
    account_label: str
    prior_shares: Optional[float]
    prior_avg_cost: Optional[float]
    new_shares: Optional[float]
    new_avg_cost: Optional[float]


@dataclass
class RunResult:
    """Summary of a synchronization run."""

    total_rows_processed: int
    updates: List[ChangeRecord]
    removals: List[ChangeRecord]
    additions: List[ChangeRecord]
    warnings: List[str]
    previous_snapshot_path: Optional[Path]
    updated_snapshot_path: Optional[Path]
    edits_log_path: Optional[Path]
    sheets_url: Optional[str]
