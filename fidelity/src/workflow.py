"""Workflow orchestration for syncing Fidelity CSV data with Google Sheets."""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Tuple

from rich.table import Table

from fidelity.src import constants, io_layer, preprocess
from fidelity.src.config import ChangeRecord, RunConfig, RunResult
from fidelity.src.datamodel import HoldingRecord, SheetRow
from fidelity.src.logging_setup import console, print_header, print_success, print_warning

FAMILY_PREFIX = "Fidelity "


def run_workflow(cfg: RunConfig) -> RunResult:
    """Run the end-to-end synchronization from CSV to Google Sheets."""

    print_header(f"{constants.APP_NAME} • {cfg.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

    warnings: List[str] = []

    try:
        raw_rows = io_layer.read_input_csv(cfg.csv_path)
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc

    holdings, preprocess_warnings = preprocess.preprocess_rows(raw_rows)
    warnings.extend(preprocess_warnings)

    holdings_map: Dict[Tuple[str, str], HoldingRecord] = {
        (record.ticker, record.account_label): record for record in holdings
    }

    try:
        table_state = io_layer.read_portfolio_table(cfg.sheet_id, cfg.tab_name)
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc

    title_row = ["Portfolio Tracker"] + [""] * (len(constants.SHEET_RANGE_COLUMNS) - 1)
    padded_snapshot_rows = [_pad_row(row) for row in table_state.raw_values]
    artifacts_dir = cfg.ensure_artifacts_dir()
    timestamp_label = cfg.timestamp.strftime('%Y%m%d-%H%M%S')
    previous_snapshot_path = artifacts_dir / f"{timestamp_label}_previous_portfolio.csv"
    io_layer.write_snapshot(
        previous_snapshot_path,
        constants.SHEET_RANGE_NAMES,
        padded_snapshot_rows,
        rows_before_header=title_row,
    )

    updates: List[ChangeRecord] = []
    removals: List[ChangeRecord] = []
    additions: List[ChangeRecord] = []
    existing_map: Dict[Tuple[str, str], Tuple[int, SheetRow]] = {}

    for idx, sheet_row in enumerate(table_state.rows):
        list_index = idx
        if _is_fidelity_row(sheet_row):
            key = (sheet_row.ticker, sheet_row.account_label)
            if key not in existing_map:
                existing_map[key] = (list_index, sheet_row)
            else:
                warnings.append(
                    "Duplicate Fidelity row detected in sheet for"
                    f" <{sheet_row.ticker} | {sheet_row.account_label}>"
                )

    # Track which indices to remove
    indices_to_remove: List[int] = []

    for key, (list_index, sheet_row) in existing_map.items():
        holding = holdings_map.get(key)
        if holding is None:
            indices_to_remove.append(list_index)
            removals.append(
                ChangeRecord(
                    action="removed",
                    ticker=sheet_row.ticker,
                    account_label=sheet_row.account_label,
                    prior_shares=sheet_row.shares,
                    prior_avg_cost=sheet_row.avg_cost,
                    new_shares=None,
                    new_avg_cost=None,
                )
            )
            continue

        raw_row = padded_snapshot_rows[list_index]
        prior_shares = sheet_row.shares
        prior_avg_cost = sheet_row.avg_cost

        updated_shares = holding.shares
        updated_avg_cost = holding.avg_cost

        if (
            _needs_update(prior_shares, updated_shares)
            or _needs_update(prior_avg_cost, updated_avg_cost)
        ):
            raw_row[1] = _format_numeric(updated_shares)  # column B
            raw_row[2] = _format_numeric(updated_avg_cost)  # column C
            raw_row[6] = sheet_row.account_label  # column G
            updates.append(
                ChangeRecord(
                    action="updated",
                    ticker=sheet_row.ticker,
                    account_label=sheet_row.account_label,
                    prior_shares=prior_shares,
                    prior_avg_cost=prior_avg_cost,
                    new_shares=updated_shares,
                    new_avg_cost=updated_avg_cost,
                )
            )
        holdings_map.pop(key, None)

    # Remove rows by deleting in reverse order to keep indices stable
    for idx in sorted(indices_to_remove, reverse=True):
        # del padded_snapshot_rows[idx]
        # instead of deleting, blank out the row to preserve formulas in other rows
        padded_snapshot_rows[idx] = [""] * len(padded_snapshot_rows[idx])

    # Add new holdings remaining in holdings_map
    start_idx = table_state.start_row_index + len(table_state.rows)
    d_template = '=IFERROR(GOOGLEFINANCE(A{row}), "")'
    e_template = '=IF(NOT(B{row}*D{row}=0), B{row}*D{row}, "")'
    f_template = '=IF(C{row}=0, "", (D{row}/C{row})-1)'
    for idx, holding in enumerate(holdings_map.values(), start=start_idx):
        new_row = ["", "", "", "", "", "", ""]
        new_row[0] = holding.ticker
        new_row[1] = _format_numeric(holding.shares)
        new_row[2] = _format_numeric(holding.avg_cost)
        new_row[3] = d_template.format(row=idx)
        new_row[4] = e_template.format(row=idx)
        new_row[5] = f_template.format(row=idx)
        new_row[6] = holding.account_label
        padded_snapshot_rows.append(new_row)
        additions.append(
            ChangeRecord(
                action="added",
                ticker=holding.ticker,
                account_label=holding.account_label,
                prior_shares=None,
                prior_avg_cost=None,
                new_shares=holding.shares,
                new_avg_cost=holding.avg_cost,
            )
        )

    if not cfg.dry_run:
        io_layer.write_portfolio_table(cfg.sheet_id, cfg.tab_name, padded_snapshot_rows)
        io_layer.sort_portfolio_table(cfg.sheet_id, cfg.tab_name, len(padded_snapshot_rows))
        print_success("Google Sheet updated successfully")
    else:
        print_warning("Dry run enabled; no Google Sheet updates were applied")

    change_log_records = [asdict(record) for record in (
        updates + additions + removals
    )]
    edits_path = artifacts_dir / f"{timestamp_label}_edits.json"
    io_layer.write_change_log(edits_path, change_log_records)

    updated_snapshot_path = artifacts_dir / f"{timestamp_label}_updated_portfolio.csv"
    io_layer.write_snapshot(updated_snapshot_path, constants.SHEET_RANGE_NAMES, padded_snapshot_rows)

    _render_summary_table(updates, additions, removals)

    for warning in warnings:
        print_warning(warning)

    if cfg.sheet_id:
        sheets_url = io_layer.sheets_url_for(cfg.sheet_id) + "?gid=1603070938#gid=1603070938"
        console.print(f"[cyan]Sheet:[/cyan] [link={sheets_url}]{sheets_url}[/link]")

    return RunResult(
        total_rows_processed=len(holdings),
        updates=updates,
        removals=removals,
        additions=additions,
        warnings=warnings,
        previous_snapshot_path=previous_snapshot_path,
        updated_snapshot_path=updated_snapshot_path,
        edits_log_path=edits_path,
        sheets_url=sheets_url,
    )


def _pad_row(row: List[str]) -> List[str]:
    padded = list(row)
    if len(padded) < len(constants.SHEET_RANGE_COLUMNS):
        padded.extend([""] * (len(constants.SHEET_RANGE_COLUMNS) - len(padded)))
    return padded[: len(constants.SHEET_RANGE_COLUMNS)]


def _is_fidelity_row(sheet_row: SheetRow) -> bool:
    return bool(sheet_row.account_label and sheet_row.account_label.startswith(FAMILY_PREFIX))


def _needs_update(old: float | None, new: float) -> bool:
    if old is None:
        return True
    return abs(old - new) > 1e-6


def _format_numeric(value: float | None) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") if value is not None else ""


def _render_summary_table(
    updates: List[ChangeRecord],
    additions: List[ChangeRecord],
    removals: List[ChangeRecord],
) -> None:
    table = Table(title="Changes Applied", show_lines=False, show_header=True)
    table.add_column("Action")
    table.add_column("Ticker")
    table.add_column("Account")
    table.add_column("Shares")
    table.add_column("Avg. Cost")

    for record in updates:
        table.add_row(
            "Update",
            record.ticker,
            record.account_label,
            _change_repr(record.prior_shares, record.new_shares),
            _change_repr(record.prior_avg_cost, record.new_avg_cost),
        )
    for record in additions:
        table.add_row(
            "Add",
            record.ticker,
            record.account_label,
            _change_repr(None, record.new_shares),
            _change_repr(None, record.new_avg_cost),
        )
    for record in removals:
        table.add_row(
            "Remove",
            record.ticker,
            record.account_label,
            _change_repr(record.prior_shares, None),
            _change_repr(record.prior_avg_cost, None),
        )

    if table.row_count:
        console.print(table)
    else:
        print_success("No fidelity rows required changes")


def _change_repr(old: float | None, new: float | None) -> str:
    parts = []
    if old is not None:
        parts.append(str(round(old, 6)))
    parts.append("→")
    if new is not None:
        parts.append(str(round(new, 6)))
    return " ".join(parts)
