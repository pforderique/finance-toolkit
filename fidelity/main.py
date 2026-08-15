"""
Command-line interface for the Fidelity portfolio synchronization tool.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.table import Table

from fidelity.src import io_layer, scheduled, sync_state
from fidelity.src.logging_setup import (
    console,
    humanize_age,
    print_success,
    print_warning,
    render_diff_table,
)
from fidelity.src.settings import (
    AccountMapping,
    Settings,
    SettingsError,
    load_settings,
    save_settings,
)
from fidelity.src.workflow import SyncGuardError, run_apply, run_dry_run, write_changes_artifact

load_dotenv()

app = typer.Typer(add_completion=False, no_args_is_help=True)
accounts_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Manage Fidelity account -> sheet label mappings.")
sheet_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Inspect the resolved Portfolio sheet/table.")
app.add_typer(accounts_app, name="accounts")
app.add_typer(sheet_app, name="sheet")


SETTINGS_OPTION = typer.Option(
    None,
    "--settings",
    help="Path to settings.toml (defaults to fidelity/settings.toml)",
)


def _load_settings_or_exit(settings_path: Optional[Path]) -> Settings:
    try:
        return load_settings(settings_path)
    except SettingsError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@sheet_app.command("info", help="Print the resolved table's gid/tableId/range/columns/capacity.")
def sheet_info(
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    sid = settings.sheet.spreadsheet_id
    tab_name = settings.sheet.tab
    table_name = settings.sheet.table

    try:
        info = io_layer.resolve_table(sid, tab_name, table_name)
        rows = io_layer.read_table_block(sid, info)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    result_table = Table(title=f"{info.tab} / {info.table_name}", show_header=False)
    result_table.add_column("Field", style="cyan")
    result_table.add_column("Value")
    result_table.add_row("Spreadsheet ID", sid)
    result_table.add_row("Tab", info.tab)
    result_table.add_row("gid (sheetId)", str(info.sheet_id))
    result_table.add_row("Table name", info.table_name)
    result_table.add_row("tableId", info.table_id)
    result_table.add_row("Range", info.range_a1)
    result_table.add_row("Header row", str(info.header_row))
    result_table.add_row("Data rows", f"{info.first_data_row}-{info.last_data_row}")
    result_table.add_row("Capacity", str(info.capacity))
    result_table.add_row("Used rows", str(len(rows)))
    result_table.add_row("Columns", ", ".join(sorted(info.column_index_by_name, key=info.column_index_by_name.get)))
    console.print(result_table)


CSV_ARGUMENT = typer.Argument(
    ..., exists=True, dir_okay=False, readable=True, help="Fidelity positions CSV export"
)


NO_COMPACT_OPTION = typer.Option(
    False,
    "--no-compact",
    help="Preserve row positions: blank deleted rows in place and reuse them for adds, "
    "instead of compacting survivors upward (default).",
)


def _dry_run(
    csv_path: Path,
    settings_path: Optional[Path],
    no_compact: bool = False,
) -> None:
    settings = _load_settings_or_exit(settings_path)

    try:
        result = run_dry_run(csv_path, settings, compact=not no_compact)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    render_diff_table(result.plan)
    for warning in result.plan.warnings:
        print_warning(warning)

    artifact_path = write_changes_artifact(
        csv_path, settings.sheet.spreadsheet_id, result.table_info, result.plan, dry_run=True, applied=False
    )
    console.print(f"Change log (dry run, not applied): {artifact_path}")

    console.print("[yellow]DRY RUN — nothing written.[/yellow]")


@app.command(help="Show a dry-run diff of a Fidelity CSV against the live Portfolio sheet. Never writes.")
def diff(
    csv_path: Path = CSV_ARGUMENT,
    no_compact: bool = NO_COMPACT_OPTION,
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    _dry_run(csv_path, settings_path, no_compact)


@app.command(help="Sync a Fidelity CSV into the Portfolio sheet. Writes by default; use --dry-run (or the diff command) to preview.")
def sync(
    csv_path: Path = CSV_ARGUMENT,
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview changes without writing"),
    allow_mass_delete: bool = typer.Option(
        False,
        "--allow-mass-delete",
        help="Confirm proceeding despite deletes exceeding the 25% mass-delete threshold",
    ),
    no_compact: bool = NO_COMPACT_OPTION,
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    if dry_run:
        _dry_run(csv_path, settings_path, no_compact)
        return

    settings = _load_settings_or_exit(settings_path)

    try:
        result = run_apply(
            csv_path,
            settings,
            compact=not no_compact,
            allow_mass_delete=allow_mass_delete,
        )
    except (SyncGuardError, RuntimeError, ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    render_diff_table(result.plan)
    for warning in result.plan.warnings:
        print_warning(warning)

    counts = result.plan.counts()
    print_success(
        f"Applied: {counts['adds']} adds, {counts['updates']} updates, "
        f"{counts['deletes']} deletes, {counts['unchanged']} unchanged, "
        f"{counts['untouched']} untouched (non-Fidelity)."
    )
    if result.before_path:
        console.print(f"Rollback snapshot: {result.before_path}")
    if result.changes_path:
        console.print(f"Change log: {result.changes_path}")


@app.command(help="Show the last successfully-applied sync: when, from which CSV, and with what counts.")
def status():
    state = sync_state.read_sync_state()
    if state is None:
        console.print("[warning]Never synced.[/warning]")
        return

    when = datetime.fromisoformat(state["timestamp"])
    age = humanize_age(when)

    result_table = Table(title="Last synced", show_header=False)
    result_table.add_column("Field", style="cyan")
    result_table.add_column("Value")
    result_table.add_row("When", f"{when.strftime('%Y-%m-%d %H:%M:%S %Z')} ({age})")
    result_table.add_row("CSV", state["csv_name"])

    counts = state["counts"]
    result_table.add_row(
        "Counts",
        f"{counts['adds']} adds, {counts['updates']} updates, {counts['deletes']} deletes, "
        f"{counts['unchanged']} unchanged, {counts['untouched']} untouched",
    )
    net_delta = state["net_equity_delta"]
    sign = "+" if net_delta > 0 else "-" if net_delta < 0 else ""
    result_table.add_row("Net equity delta", f"{sign}${abs(net_delta):,.2f}")

    console.print(result_table)


@app.command(
    "scheduled-sync",
    help="Unattended gated sync for the LaunchAgent: finds the newest CSV, dry runs, "
         "and only writes if the plan is under the [schedule] delta threshold. "
         "Emails instead of writing when a gate trips.",
)
def scheduled_sync(
    settings_path: Optional[Path] = SETTINGS_OPTION,
    no_compact: bool = NO_COMPACT_OPTION,
    force: bool = typer.Option(
        False, "--force", help="Bypass the net-equity-delta tripwire (guards still apply)"
    ),
):
    settings = _load_settings_or_exit(settings_path)
    result = scheduled.run_scheduled(settings, compact=not no_compact, force=force)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"[{stamp}] scheduled-sync: {result.status} - {result.reason}")
    if result.counts is not None:
        console.print(
            f"  {result.counts['adds']} adds, {result.counts['updates']} updates, "
            f"{result.counts['deletes']} deletes, net {result.net_equity_delta:+,.2f}"
        )
    if result.emailed:
        console.print("  (alert emailed)")
    elif result.status in ("skipped", "error"):
        console.print("[warning]  (alert NOT emailed - check EMAIL_* env vars)[/warning]")

    raise typer.Exit(code=result.exit_code)


@accounts_app.command("list", help="List all account -> sheet-label mappings.")
def accounts_list(
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    table = Table(title=f"Accounts ({settings.path})", show_lines=False, show_header=True)
    table.add_column("Number")
    table.add_column("Name")
    table.add_column("Label")
    table.add_column("Enabled")

    for acct in settings.accounts:
        table.add_row(
            acct.number,
            acct.name,
            acct.label,
            "[green]yes[/green]" if acct.enabled else "[red]no[/red]",
        )

    if not settings.accounts:
        console.print("[warning]No accounts configured.[/warning]")
    else:
        console.print(table)


@accounts_app.command("labels", help="Live-read the valid Account dropdown labels from the sheet.")
def accounts_labels(
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    try:
        labels = io_layer.read_account_dropdown_labels(settings.sheet.spreadsheet_id)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="Valid Account dropdown labels", show_header=True)
    table.add_column("Label")
    for label in labels:
        table.add_row(label)
    console.print(table)


@accounts_app.command("add", help="Add a new account mapping.")
def accounts_add(
    number: str = typer.Option(..., "--number", help="Fidelity account number"),
    name: str = typer.Option(..., "--name", help="Fidelity 'Account name' as it appears in the CSV"),
    label: str = typer.Option(..., "--label", help="Sheet Account dropdown label to map to"),
    disabled: bool = typer.Option(False, "--disabled", help="Add the account as disabled"),
    force: bool = typer.Option(
        False, "--force", help="Skip validating --label against the live sheet dropdown"
    ),
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    if not force:
        _validate_label_or_exit(settings, label)

    try:
        settings.add_account(
            AccountMapping(number=number, name=name, label=label, enabled=not disabled)
        )
        save_settings(settings)
    except SettingsError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[success]Added account {number} ({name}) -> {label}[/success]")


@accounts_app.command("edit", help="Edit an existing account mapping.")
def accounts_edit(
    number: str = typer.Argument(..., help="Account number to edit"),
    name: Optional[str] = typer.Option(None, "--name", help="New Fidelity 'Account name'"),
    label: Optional[str] = typer.Option(None, "--label", help="New sheet Account dropdown label"),
    enable: bool = typer.Option(False, "--enable", help="Enable the account"),
    disable: bool = typer.Option(False, "--disable", help="Disable the account"),
    force: bool = typer.Option(
        False, "--force", help="Skip validating --label against the live sheet dropdown"
    ),
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    if enable and disable:
        console.print("[red]Error:[/red] --enable and --disable are mutually exclusive.")
        raise typer.Exit(code=2)

    acct = settings.find_by_number(number)
    if acct is None:
        console.print(f"[red]Error:[/red] No account with number '{number}' in {settings.path}")
        raise typer.Exit(code=1)

    if label is not None and not force:
        _validate_label_or_exit(settings, label)

    if name is not None:
        acct.name = name
    if label is not None:
        acct.label = label
    if enable:
        acct.enabled = True
    if disable:
        acct.enabled = False

    try:
        save_settings(settings)
    except SettingsError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[success]Updated account {number}[/success]")


@accounts_app.command("remove", help="Remove an account mapping.")
def accounts_remove(
    number: str = typer.Argument(..., help="Account number to remove"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    settings_path: Optional[Path] = SETTINGS_OPTION,
):
    settings = _load_settings_or_exit(settings_path)

    acct = settings.find_by_number(number)
    if acct is None:
        console.print(f"[red]Error:[/red] No account with number '{number}' in {settings.path}")
        raise typer.Exit(code=1)

    if not yes:
        confirmed = typer.confirm(f"Remove account {number} ({acct.name} -> {acct.label})?")
        if not confirmed:
            console.print("Aborted.")
            raise typer.Exit(code=0)

    try:
        settings.remove_account(number)
        save_settings(settings)
    except SettingsError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[success]Removed account {number}[/success]")


def _validate_label_or_exit(settings: Settings, label: str) -> None:
    try:
        valid_labels = io_layer.read_account_dropdown_labels(settings.sheet.spreadsheet_id)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if label not in valid_labels:
        console.print(
            f"[red]Error:[/red] '{label}' is not a valid Account dropdown label. "
            f"Valid labels: {', '.join(valid_labels)}. Use --force to bypass this check."
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
