"""Logging setup and utility functions for the screener application."""

import datetime as _dt
import logging
from typing import Iterable

from rich.console import Console
from rich.panel import Panel

console = Console()
logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Configure the root logger to the specified level."""
    level_name = level.upper()
    numeric_level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

def timestamp() -> str:
    """Return the current timestamp as a formatted string."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_header(title: str) -> None:
    """Print a formatted header to the console."""
    console.print(Panel.fit(title, border_style="cyan"))


def print_warnings(warnings: Iterable[str]) -> None:
    """Print a list of warnings to the console."""
    items = list(warnings)
    if not items:
        return
    body = "\n".join(f"• {msg}" for msg in items)
    console.print(Panel(body, title="Warnings", border_style="yellow"))


def alert_fmv_change(count: int, tab_name: str, dry_run: bool = False) -> None:
    """Log FMV changes detected. Extensible for future alerting."""
    prefix = "[DRY RUN] " if dry_run else ""
    logger.info(f"{prefix}• FMV history appended: {count} changes → tab {tab_name}")
