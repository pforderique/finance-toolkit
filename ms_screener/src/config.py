"""Configuration dataclasses and constants for the screener tool."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).parent.parent
DEFAULT_OUT_DIR = ROOT_DIR / "out"
DEFAULT_DATA_DIR = ROOT_DIR / "data"
DEFAULT_COMPARE_BATCH_SIZE = 50
DEFAULT_DATA_TAB = "collected_data"
DEFAULT_SNAPSHOT_TAB = "Screener"
DEFAULT_CHANGES_TAB = "FairValueChanges"
DEFAULT_FMV_HISTORY_TAB = "Data_Changes"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_AUTO_HEADLESS = True


@dataclass
class RunConfig:
    """Configuration for a single run of the screener tool."""
    sheet_id: Optional[str] = None
    data_tab: str = DEFAULT_DATA_TAB
    folder: Optional[Path] = None
    files: List[Path] = field(default_factory=list)
    snapshot_tab: str = DEFAULT_SNAPSHOT_TAB
    changes_tab: str = DEFAULT_CHANGES_TAB
    fmv_history_tab: str = DEFAULT_FMV_HISTORY_TAB
    dry_run: bool = False
    log_level: str = DEFAULT_LOG_LEVEL
    out_dir: Path = DEFAULT_OUT_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    compare_batch_size: int = DEFAULT_COMPARE_BATCH_SIZE
    auto: bool = False
    auto_headless: bool = DEFAULT_AUTO_HEADLESS
    scrape_individual: bool = False
    scrape_max_stocks: int = 20
    scrape_rate_limit: float = 3.0
    scrape_tickers: List[str] = field(default_factory=list)
    scrape_only: bool = False


@dataclass
class RunResult:
    """Result summary from a single run of the screener tool."""
    total_files: int
    rows_ingested: int
    rows_snapshot: int
    rows_fmv_changes: int
    warnings: List[str]
    compare_links_path: Optional[Path]
    snapshot_csv_path: Optional[Path]
    fmv_changes_csv_path: Optional[Path]
    sheets_url: Optional[str]
