"""
Command-line interface for the Fidelity portfolio synchronization tool.
"""

import os
from pathlib import Path

import typer
from dotenv import load_dotenv

from fidelity.src import constants
from fidelity.src.config import RunConfig
from fidelity.src.logging_setup import configure_logging, console
from fidelity.src.workflow import run_workflow

load_dotenv()

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command(help="Sync a Fidelity positions CSV into the Portfolio Tracker sheet.")
def run(
    csv_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="Fidelity CSV export"
    ),
    sheet_id: str = typer.Option(
        os.getenv(constants.ENV_SHEET_ID),
        "--sheet-id",
        help="Google Sheet ID for the Portfolio Tracker tab",
    ),
    tab_name: str = typer.Option(
        constants.PORTFOLIO_TAB,
        "--tab-name",
        help="Sheet tab name to update",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Process everything but skip the Sheets update",
    ),
    log_level: str = typer.Option(
        constants.DEFAULT_LOG_LEVEL,
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    ),
):
    """Run the synchronization workflow."""

    configure_logging(log_level)

    if not sheet_id:
        console.print(
            "[red]Error:[/red] Missing sheet id. Provide --sheet-id or set the SHEET_ID environment variable."
        )
        raise typer.Exit(code=2)

    cfg = RunConfig(
        csv_path=csv_path,
        sheet_id=sheet_id,
        tab_name=tab_name,
        dry_run=dry_run,
        log_level=log_level.upper(),
    )

    try:
        run_workflow(cfg)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
