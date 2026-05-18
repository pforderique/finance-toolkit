"""
Command-line interface for the Morningstar screener tool.
"""
import os
from pathlib import Path
from typing import List, Optional

import typer
from dotenv import load_dotenv

from ms_screener.src.config import RunConfig
from ms_screener.src.logging_setup import configure_logging, console
from ms_screener.src.workflow import run_workflow

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command(help="Run the Morningstar workflow end-to-end.")
def run(
    sheet_id: str = typer.Option(
        os.getenv("SHEET_ID"), "--sheet-id", help="Google Sheet ID for 'collected_data' tab"),
    data_tab: str = typer.Option(
        "collected_data", "--data-tab", help="Tab name for collected inputs"),
    folder: Optional[Path] = typer.Option(
        None, "--folder", exists=False, help="Folder of Morningstar CSVs"),
    files: List[Path] = typer.Option(
        None, "--files", help="Explicit Morningstar CSV files"),
    snapshot_tab: str = typer.Option(
        "Screener", "--snapshot-tab", help="Output tab for snapshot (Sheets)"),
    changes_tab: str = typer.Option(
        "FMV_Tracker", "--changes-tab", help="Output tab for fair value deltas (Sheets)"),
    fmv_history_tab: str = typer.Option(
        "FMV_History", "--fmv-history-tab", help="Output tab for FMV history (append-only, Sheets)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Do everything except write to Google Sheets"),
    log_level: str = typer.Option(
        "INFO", "--log-level", help="Log verbosity: DEBUG/INFO/WARN/ERROR"),
    auto: bool = typer.Option(
        False, "--auto", help="Automatically log in and download Morningstar CSVs"),
    auto_headless: bool = typer.Option(
        True,
        "--auto-headless/--auto-visible",
        help="Run the automation headless (default) or show the browser window",
    ),
):
    """Run the Morningstar workflow end-to-end."""
    configure_logging(log_level)

    cfg = RunConfig(
        sheet_id=sheet_id,
        data_tab=data_tab,
        folder=folder,
        files=files or [],
        snapshot_tab=snapshot_tab,
        changes_tab=changes_tab,
        fmv_history_tab=fmv_history_tab,
        dry_run=dry_run,
        log_level=log_level.upper(),
        auto=auto,
        auto_headless=auto_headless,
    )

    try:
        run_workflow(cfg)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


if __name__ == "__main__":
    load_dotenv()
    app()
