"""Diff engine (pure) and the read+diff orchestration for `fidelity sync` / `fidelity diff`.

`plan_changes` is a pure function -- zero I/O -- so it's directly unit-testable with
hand-built fixtures. All Sheets/CSV I/O lives in `run_dry_run`, which composes
`io_layer` + `preprocess` + `plan_changes` for the CLI.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from fidelity.src import constants, io_layer, preprocess, sync_state
from fidelity.src.datamodel import (
    ChangeEntry,
    ChangePlan,
    HoldingKey,
    HoldingRecord,
    SheetRow,
    TableInfo,
    TargetRow,
)
from fidelity.src.settings import Settings

# Deletes beyond this fraction of currently-owned sheet rows require an
# explicit --yes or --allow-mass-delete (guard #4). A partial/garbled export
# should never be able to silently wipe most of an account.
MASS_DELETE_THRESHOLD_RATIO = 0.25


class SyncGuardError(RuntimeError):
    """A pre-flight safety guard failed. Always fatal -- the write is never attempted."""


def plan_changes(
    sheet_rows: List[SheetRow],
    holdings: List[HoldingRecord],
    settings: Settings,
    observed_labels: Set[str],
    skipped_keys: Set[HoldingKey],
) -> ChangePlan:
    """Pure diff engine. No I/O.

    in_scope(row) <=> row.account_label in (owned_labels & observed_labels).
    This is BOTH data-loss guards at once:
      - skipped_keys suppresses deletion for CSV rows seen but unparsable.
      - observed_labels narrows the delete scope to accounts actually present
        in this CSV, so a partial export can't mass-delete accounts it never
        mentioned.
    """

    owned = settings.owned_labels()
    scope = owned & observed_labels

    # Defense in depth: only holdings for in-scope labels are eligible to become
    # ADDs. In the real pipeline this filter is a no-op (preprocess_rows only
    # ever resolves labels via settings.resolve_label, which already restricts
    # to enabled accounts), but plan_changes should not rely on that invariant
    # holding at every call site.
    holdings_map: Dict[HoldingKey, HoldingRecord] = {
        (h.ticker, h.account_label): h for h in holdings if h.account_label in scope
    }

    plan = ChangePlan()
    seen_in_scope: Dict[HoldingKey, SheetRow] = {}

    for row in sheet_rows:
        if row.account_label not in scope:
            plan.untouched.append(
                ChangeEntry(
                    action="untouched",
                    ticker=row.ticker,
                    account_label=row.account_label,
                    row_number=row.row_number,
                    old_shares=row.shares,
                    old_avg_cost=row.avg_cost,
                    new_shares=row.shares,
                    new_avg_cost=row.avg_cost,
                )
            )
            continue

        key: HoldingKey = (row.ticker, row.account_label)

        if key in seen_in_scope:
            first = seen_in_scope[key]
            plan.deletes.append(
                ChangeEntry(
                    action="delete",
                    ticker=row.ticker,
                    account_label=row.account_label,
                    row_number=row.row_number,
                    old_shares=row.shares,
                    old_avg_cost=row.avg_cost,
                    new_shares=None,
                    new_avg_cost=None,
                )
            )
            plan.warnings.append(
                f"Duplicate in-scope sheet row for {row.ticker}/{row.account_label} at "
                f"row {row.row_number}; keeping row {first.row_number}, deleting this one"
            )
            continue

        seen_in_scope[key] = row
        holding = holdings_map.pop(key, None)

        if holding is None:
            if key in skipped_keys:
                # Protected: this row was seen in the CSV but couldn't be parsed.
                # Treat as unchanged rather than risk a data-loss delete.
                plan.unchanged.append(
                    ChangeEntry(
                        action="unchanged",
                        ticker=row.ticker,
                        account_label=row.account_label,
                        row_number=row.row_number,
                        old_shares=row.shares,
                        old_avg_cost=row.avg_cost,
                        new_shares=row.shares,
                        new_avg_cost=row.avg_cost,
                    )
                )
            else:
                plan.deletes.append(
                    ChangeEntry(
                        action="delete",
                        ticker=row.ticker,
                        account_label=row.account_label,
                        row_number=row.row_number,
                        old_shares=row.shares,
                        old_avg_cost=row.avg_cost,
                        new_shares=None,
                        new_avg_cost=None,
                    )
                )
            continue

        shares_delta = abs((row.shares or 0.0) - holding.shares)
        avg_cost_delta = abs((row.avg_cost or 0.0) - holding.avg_cost)

        if shares_delta > settings.tolerance.shares or avg_cost_delta > settings.tolerance.avg_cost:
            plan.updates.append(
                ChangeEntry(
                    action="update",
                    ticker=row.ticker,
                    account_label=row.account_label,
                    row_number=row.row_number,
                    old_shares=row.shares,
                    old_avg_cost=row.avg_cost,
                    new_shares=holding.shares,
                    new_avg_cost=holding.avg_cost,
                )
            )
        else:
            plan.unchanged.append(
                ChangeEntry(
                    action="unchanged",
                    ticker=row.ticker,
                    account_label=row.account_label,
                    row_number=row.row_number,
                    old_shares=row.shares,
                    old_avg_cost=row.avg_cost,
                    new_shares=holding.shares,
                    new_avg_cost=holding.avg_cost,
                )
            )

    # Anything left in holdings_map had no matching in-scope sheet row -> add.
    for (ticker, label), holding in holdings_map.items():
        plan.adds.append(
            ChangeEntry(
                action="add",
                ticker=ticker,
                account_label=label,
                row_number=None,
                old_shares=None,
                old_avg_cost=None,
                new_shares=holding.shares,
                new_avg_cost=holding.avg_cost,
            )
        )

    return plan


def build_target_block(
    table_info: TableInfo,
    sheet_rows: List[SheetRow],
    plan: ChangePlan,
    compact: bool = True,
) -> List[TargetRow]:
    """Materialize the full `table_info.capacity`-row logical A/B/C/G block, in
    final physical row order. Pure -- no I/O.

    Dry run and apply both call this, so there is exactly one code path that
    decides what the sheet should look like -- a dry run's preview and an
    apply's write can never drift apart.

    Compaction (default) is safe specifically because column D is a single
    Ticker-keyed spilled ARRAYFORMULA (position independent) and E/F are
    self-row formulas pre-filled through the last capacity row -- moving a
    row from slot 40 to slot 39 is a semantic no-op. `--no-compact` instead
    blanks deleted rows in place and reuses those (and any never-used) slots
    for adds, only extending past the last used row if it runs out of blanks.

    Raises SyncGuardError (guard #1) if the plan needs more rows than the
    table currently has capacity for.
    """

    delete_rows = {e.row_number for e in plan.deletes}
    update_by_row = {e.row_number: e for e in plan.updates}
    adds_sorted = sorted(plan.adds, key=lambda e: (e.account_label, e.ticker))

    needed = len(sheet_rows) - len(delete_rows) + len(adds_sorted)
    if needed > table_info.capacity:
        raise SyncGuardError(
            f"Table '{table_info.table_name}' has room for {table_info.capacity} rows, "
            f"plan needs {needed}. Extend {table_info.table_name} in the sheet UI (drag its "
            "bottom edge) and re-run."
        )

    if compact:
        survivors: List[TargetRow] = []
        for row in sheet_rows:
            if row.row_number in delete_rows:
                continue
            entry = update_by_row.get(row.row_number)
            if entry is not None:
                survivors.append(
                    TargetRow(row.ticker, entry.new_shares, entry.new_avg_cost, row.account_label)
                )
            else:
                survivors.append(TargetRow(row.ticker, row.shares, row.avg_cost, row.account_label))

        for entry in adds_sorted:
            survivors.append(
                TargetRow(entry.ticker, entry.new_shares, entry.new_avg_cost, entry.account_label)
            )

        while len(survivors) < table_info.capacity:
            survivors.append(TargetRow("", None, None, ""))

        return survivors

    # --no-compact: keep every surviving row in its current physical slot;
    # deletes leave that slot blank; adds fill blank slots (lowest row first,
    # which includes both freshly-deleted slots and never-used capacity)
    # before any slot would need to move.
    slots: Dict[int, Optional[TargetRow]] = {offset: None for offset in range(table_info.capacity)}
    for row in sheet_rows:
        if row.row_number in delete_rows:
            continue
        offset = row.row_number - table_info.first_data_row
        entry = update_by_row.get(row.row_number)
        if entry is not None:
            slots[offset] = TargetRow(row.ticker, entry.new_shares, entry.new_avg_cost, row.account_label)
        else:
            slots[offset] = TargetRow(row.ticker, row.shares, row.avg_cost, row.account_label)

    free_offsets = sorted(offset for offset, value in slots.items() if value is None)
    for entry, offset in zip(adds_sorted, free_offsets):
        slots[offset] = TargetRow(entry.ticker, entry.new_shares, entry.new_avg_cost, entry.account_label)

    return [slots[offset] or TargetRow("", None, None, "") for offset in range(table_info.capacity)]


@dataclass
class DryRunResult:
    table_info: TableInfo
    sheet_rows: List[SheetRow]
    holdings: List[HoldingRecord]
    plan: ChangePlan
    target_rows: Optional[List[TargetRow]] = None


def run_dry_run(
    csv_path: Path,
    settings: Settings,
    spreadsheet_id: Optional[str] = None,
    tab: Optional[str] = None,
    table: Optional[str] = None,
    compact: bool = True,
) -> DryRunResult:
    """I/O-performing orchestration: read CSV + sheet, run the pure diff engine.

    Read-only Sheets calls only -- never writes. This is exactly what backs both
    `fidelity diff` and `fidelity sync --dry-run`, and it's also the
    first half of `run_apply` -- the plan a write applies is the plan a dry
    run showed.
    """

    sid = spreadsheet_id or settings.sheet.spreadsheet_id
    tab_name = tab or settings.sheet.tab
    table_name = table or settings.sheet.table

    raw_rows = io_layer.read_input_csv(csv_path)
    holdings, skipped_keys, observed_labels, csv_warnings = preprocess.preprocess_rows(
        raw_rows, settings
    )

    table_info = io_layer.resolve_table(sid, tab_name, table_name)
    sheet_rows = io_layer.read_table_block(sid, table_info)

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, skipped_keys)
    plan.warnings = list(csv_warnings) + plan.warnings

    target_rows: Optional[List[TargetRow]] = None
    try:
        target_rows = build_target_block(table_info, sheet_rows, plan, compact=compact)
    except SyncGuardError as exc:
        # Surfaced as a warning rather than raised here -- capacity is only a
        # FATAL guard when actually writing (`run_apply`); a plain dry run
        # should still show the diff and let the user see the problem.
        plan.warnings.append(str(exc))

    return DryRunResult(
        table_info=table_info,
        sheet_rows=sheet_rows,
        holdings=holdings,
        plan=plan,
        target_rows=target_rows,
    )


def _csv_sha256(csv_path: Path) -> str:
    return hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()


def _entry_to_dict(entry: ChangeEntry) -> Dict:
    return {
        "action": entry.action,
        "ticker": entry.ticker,
        "account_label": entry.account_label,
        "row": entry.row_number,
        "prior_shares": entry.old_shares,
        "prior_avg_cost": entry.old_avg_cost,
        "new_shares": entry.new_shares,
        "new_avg_cost": entry.new_avg_cost,
    }


def build_changes_payload(
    csv_path: Path,
    spreadsheet_id: str,
    table_info: TableInfo,
    plan: ChangePlan,
    dry_run: bool,
    applied: bool,
) -> Dict:
    """The `out/<ts>_changes.json` document. Same shape whether it's from a
    dry run (`applied=False`) or a real apply."""
    return {
        "spreadsheet_id": spreadsheet_id,
        "tab": table_info.tab,
        "table_id": table_info.table_id,
        "table_range": table_info.range_a1,
        "csv_path": str(csv_path),
        "csv_sha256": _csv_sha256(csv_path),
        "dry_run": dry_run,
        "applied": applied,
        "counts": plan.counts(),
        "changes": [_entry_to_dict(e) for e in plan.all_actionable()],
        "warnings": list(plan.warnings),
    }


def write_changes_artifact(
    csv_path: Path,
    spreadsheet_id: str,
    table_info: TableInfo,
    plan: ChangePlan,
    dry_run: bool,
    applied: bool,
    artifacts_dir: Optional[Path] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Write `out/<ts>_changes.json`. Called on dry runs (applied=False) and
    on applies (applied=True) alike -- same payload shape either way."""
    out_dir = Path(artifacts_dir) if artifacts_dir is not None else constants.ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{ts}_changes.json"
    payload = build_changes_payload(csv_path, spreadsheet_id, table_info, plan, dry_run, applied)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_before_artifact(
    spreadsheet_id: str,
    table_info: TableInfo,
    artifacts_dir: Optional[Path] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Write `out/<ts>_before.json`: a fresh raw read of the full A:G block,
    taken immediately before the write. THE rollback artifact -- restoring
    from it is a `values.batchUpdate` of the same A:C/G ranges using the
    `values` captured here."""
    out_dir = Path(artifacts_dir) if artifacts_dir is not None else constants.ARTIFACTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{ts}_before.json"
    raw_values = io_layer.read_raw_values(spreadsheet_id, table_info.range_a1)
    payload = {
        "spreadsheet_id": spreadsheet_id,
        "tab": table_info.tab,
        "table_id": table_info.table_id,
        "range": table_info.range_a1,
        "captured_at": ts,
        "values": raw_values,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@dataclass
class SyncResult:
    table_info: TableInfo
    sheet_rows: List[SheetRow]
    holdings: List[HoldingRecord]
    plan: ChangePlan
    target_rows: List[TargetRow]
    applied: bool
    changes_path: Optional[Path] = None
    before_path: Optional[Path] = None


def run_apply(
    csv_path: Path,
    settings: Settings,
    spreadsheet_id: Optional[str] = None,
    tab: Optional[str] = None,
    table: Optional[str] = None,
    compact: bool = True,
    yes: bool = False,
    allow_mass_delete: bool = False,
    write_artifacts: bool = True,
    artifacts_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
) -> SyncResult:
    """Recompute the plan, run every pre-flight guard, and -- only if all of
    them pass -- issue the single write. Guard order matches the plan:

      1. capacity            (raised inside build_target_block)
      2. label validity      (every written Account label is in the live dropdown)
      3. re-read & compare   (optimistic concurrency: sheet must be unchanged
                              since the plan above was computed)
      4. mass-delete         (deletes > 25% of owned rows need --yes/--allow-mass-delete)

    All four are FATAL (raise SyncGuardError) -- none of them are warnings.
    """

    dry = run_dry_run(csv_path, settings, spreadsheet_id=spreadsheet_id, tab=tab, table=table, compact=compact)
    sid = spreadsheet_id or settings.sheet.spreadsheet_id

    # Guard #1 (capacity) already ran inside run_dry_run's build_target_block
    # call; re-run it here so a capacity failure is FATAL for an apply (it was
    # only a warning for a plain dry run).
    target_rows = build_target_block(dry.table_info, dry.sheet_rows, dry.plan, compact=compact)

    # Guard #2: every label about to be written must be a valid live dropdown value.
    valid_labels = set(io_layer.read_account_dropdown_labels(sid))
    bad_labels = sorted(
        {row.account_label for row in target_rows if row.account_label and row.account_label not in valid_labels}
    )
    if bad_labels:
        raise SyncGuardError(
            f"Refusing to write: label(s) not present in the live Account dropdown "
            f"(_Helper[Asset_Holdings]): {bad_labels}. Fix fidelity/settings.toml or the "
            "sheet's dropdown source and re-run."
        )

    # Guard #3: optimistic concurrency. Re-read the block right before writing
    # and compare against the snapshot the plan above was computed from -- a
    # human (or another run) may have edited the sheet in between.
    rechecked_rows = io_layer.read_table_block(sid, dry.table_info)
    if rechecked_rows != dry.sheet_rows:
        raise SyncGuardError(
            "The sheet changed since this plan was computed (optimistic concurrency "
            "check failed) -- someone may be editing it right now. Re-run "
            "`fidelity sync` to recompute against the current state."
        )

    # Guard #4: mass-delete threshold.
    owned_row_count = sum(1 for row in dry.sheet_rows if row.account_label in settings.owned_labels())
    threshold = math.floor(owned_row_count * MASS_DELETE_THRESHOLD_RATIO)
    if len(dry.plan.deletes) > threshold and not (yes or allow_mass_delete):
        raise SyncGuardError(
            f"Plan deletes {len(dry.plan.deletes)} row(s), exceeding the mass-delete "
            f"threshold of {threshold} ({int(MASS_DELETE_THRESHOLD_RATIO * 100)}% of "
            f"{owned_row_count} owned rows). Pass --yes or --allow-mass-delete to proceed "
            "if this is expected."
        )

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    before_path: Optional[Path] = None
    if write_artifacts:
        # Captured immediately before the write -- this IS the rollback state.
        before_path = write_before_artifact(sid, dry.table_info, artifacts_dir=artifacts_dir, timestamp=ts)

    io_layer.write_table_block(sid, dry.table_info, target_rows)

    # Record local last-synced state (never on a dry run/diff -- only here,
    # after a real write has actually succeeded).
    sync_state.write_sync_state(
        csv_name=Path(csv_path).name,
        counts=dry.plan.counts(),
        net_equity_delta=dry.plan.net_equity_delta(),
        path=state_path,
        timestamp=datetime.now().astimezone().isoformat(),
    )

    changes_path: Optional[Path] = None
    if write_artifacts:
        changes_path = write_changes_artifact(
            csv_path, sid, dry.table_info, dry.plan, dry_run=False, applied=True,
            artifacts_dir=artifacts_dir, timestamp=ts,
        )

    return SyncResult(
        table_info=dry.table_info,
        sheet_rows=dry.sheet_rows,
        holdings=dry.holdings,
        plan=dry.plan,
        target_rows=target_rows,
        applied=True,
        changes_path=changes_path,
        before_path=before_path,
    )
