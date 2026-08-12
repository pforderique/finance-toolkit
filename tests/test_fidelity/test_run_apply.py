"""Unit tests for `workflow.run_apply` -- the four pre-flight guards.

All Sheets I/O is monkeypatched (`fidelity.src.workflow.io_layer.*`); nothing
here touches the network. Guard order under test: capacity, label validity,
optimistic concurrency (re-read & compare), mass-delete threshold.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from fidelity.src import workflow
from fidelity.src.datamodel import SheetRow, TableInfo
from fidelity.src.settings import load_settings
from fidelity.src.workflow import SyncGuardError, run_apply

SETTINGS_TOML = """
[sheet]
spreadsheet_id = "SHEET123"
tab = "Portfolio"
table = "INVESTMENT_HOLDINGS"

[symbols]
ignore_prefixes = ["SPAXX", "FDRXX"]
ignore_exact = ["PENDING ACTIVITY"]
aliases = {}

[tolerance]
shares = 1e-6
avg_cost = 0.005

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""

CSV_HEADER = "Account number,Account name,Symbol,Description,Quantity,Average cost basis,Cost basis total,\n"


def _write_csv(tmp_path: Path, rows: List[str]) -> Path:
    path = tmp_path / "positions.csv"
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return path


def _table_info(capacity: int) -> TableInfo:
    return TableInfo(
        sheet_id=417815611,
        table_id="749296102",
        table_name="INVESTMENT_HOLDINGS",
        tab="Portfolio",
        header_row=6,
        first_data_row=7,
        last_data_row=6 + capacity,
        capacity=capacity,
        column_index_by_name={
            "Ticker": 0,
            "Shares": 1,
            "Avg_Cost": 2,
            "Mkt_Price": 3,
            "Total_Equity": 4,
            "Pct_Gain": 5,
            "Account": 6,
        },
        range_a1=f"Portfolio!A7:G{6 + capacity}",
    )


@pytest.fixture
def settings(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(SETTINGS_TOML, encoding="utf-8")
    return load_settings(path)


def _patch_common(monkeypatch, table_info, sheet_rows_sequence):
    """Patch resolve_table + read_table_block (called twice: once for the
    plan, once for guard #3's re-read) + read_account_dropdown_labels."""
    monkeypatch.setattr(workflow.io_layer, "resolve_table", lambda *a, **k: table_info)

    calls = {"n": 0}

    def fake_read_table_block(spreadsheet_id, ti):
        idx = min(calls["n"], len(sheet_rows_sequence) - 1)
        calls["n"] += 1
        return sheet_rows_sequence[idx]

    monkeypatch.setattr(workflow.io_layer, "read_table_block", fake_read_table_block)
    monkeypatch.setattr(
        workflow.io_layer, "read_account_dropdown_labels", lambda *a, **k: ["Fidelity Brokerage"]
    )


def test_guard1_capacity_is_fatal_on_apply(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=1)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    _patch_common(monkeypatch, table_info, [sheet_rows, sheet_rows])

    csv_path = _write_csv(
        tmp_path,
        [
            "111,Individual,AAPL,Apple,1,100,100,\n",
            "111,Individual,NVDA,Nvidia,1,100,100,\n",
        ],
    )

    with pytest.raises(SyncGuardError, match="room for|capacity"):
        run_apply(csv_path, settings, write_artifacts=False)


def test_guard2_label_not_in_live_dropdown_is_fatal(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=5)
    sheet_rows: List[SheetRow] = []
    _patch_common(monkeypatch, table_info, [sheet_rows, sheet_rows])
    # Live dropdown no longer contains "Fidelity Brokerage".
    monkeypatch.setattr(workflow.io_layer, "read_account_dropdown_labels", lambda *a, **k: ["Fidelity HSA"])

    csv_path = _write_csv(tmp_path, ["111,Individual,AAPL,Apple,1,100,100,\n"])

    with pytest.raises(SyncGuardError, match="dropdown"):
        run_apply(csv_path, settings, write_artifacts=False)


def test_guard3_concurrent_modification_is_fatal(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=5)
    original = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    # Someone edits the sheet between the plan read and the pre-write re-read.
    changed = [SheetRow("AAPL", "Fidelity Brokerage", 999.0, 100.0, row_number=7)]
    _patch_common(monkeypatch, table_info, [original, changed])

    csv_path = _write_csv(tmp_path, ["111,Individual,AAPL,Apple,2,100,200,\n"])

    with pytest.raises(SyncGuardError, match="changed since|concurrency"):
        run_apply(csv_path, settings, write_artifacts=False)


def test_guard4_mass_delete_requires_yes_or_allow_flag(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=10)
    # 4 owned rows in the sheet; CSV observes the account but lists none of
    # them -> all 4 become deletes, well past the 25% threshold (=1 row).
    sheet_rows = [
        SheetRow("A", "Fidelity Brokerage", 1.0, 1.0, row_number=7),
        SheetRow("B", "Fidelity Brokerage", 1.0, 1.0, row_number=8),
        SheetRow("C", "Fidelity Brokerage", 1.0, 1.0, row_number=9),
        SheetRow("D", "Fidelity Brokerage", 1.0, 1.0, row_number=10),
    ]
    _patch_common(monkeypatch, table_info, [sheet_rows, sheet_rows])

    # A CSV row for a DIFFERENT ticker in the same owned account, so the
    # account label is "observed" (in scope) but none of A/B/C/D appear.
    csv_path = _write_csv(tmp_path, ["111,Individual,ZZZZ,Zeta,1,1,1,\n"])

    with pytest.raises(SyncGuardError, match="mass-delete|threshold"):
        run_apply(csv_path, settings, write_artifacts=False)

    # Passing --yes clears guard #4; patch the write itself to a no-op so the
    # test doesn't touch the network for the final write call.
    monkeypatch.setattr(workflow.io_layer, "write_table_block", lambda *a, **k: {"ok": True})
    result = run_apply(csv_path, settings, write_artifacts=False, yes=True)
    assert result.applied is True
    assert len(result.plan.deletes) == 4


def test_guard4_allow_mass_delete_flag_also_clears_guard(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=10)
    sheet_rows = [
        SheetRow("A", "Fidelity Brokerage", 1.0, 1.0, row_number=7),
        SheetRow("B", "Fidelity Brokerage", 1.0, 1.0, row_number=8),
    ]
    _patch_common(monkeypatch, table_info, [sheet_rows, sheet_rows])
    monkeypatch.setattr(workflow.io_layer, "write_table_block", lambda *a, **k: {"ok": True})

    csv_path = _write_csv(tmp_path, ["111,Individual,ZZZZ,Zeta,1,1,1,\n"])

    result = run_apply(csv_path, settings, write_artifacts=False, allow_mass_delete=True)
    assert result.applied is True


def test_successful_apply_writes_artifacts(tmp_path, settings, monkeypatch):
    table_info = _table_info(capacity=5)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    _patch_common(monkeypatch, table_info, [sheet_rows, sheet_rows])

    write_calls = []
    monkeypatch.setattr(
        workflow.io_layer, "write_table_block", lambda sid, ti, rows: write_calls.append(rows) or {"ok": True}
    )
    monkeypatch.setattr(workflow.io_layer, "read_raw_values", lambda *a, **k: [["AAPL", 1.0, 100.0, "", "", "", "Fidelity Brokerage"]])

    csv_path = _write_csv(tmp_path, ["111,Individual,AAPL,Apple,2,100,200,\n"])
    artifacts_dir = tmp_path / "out"

    result = run_apply(csv_path, settings, artifacts_dir=artifacts_dir)

    assert result.applied is True
    assert len(write_calls) == 1
    assert result.before_path is not None and result.before_path.exists()
    assert result.changes_path is not None and result.changes_path.exists()

    import json

    changes = json.loads(result.changes_path.read_text())
    assert changes["applied"] is True
    assert changes["dry_run"] is False
    assert changes["counts"]["updates"] == 1

    before = json.loads(result.before_path.read_text())
    assert before["values"] == [["AAPL", 1.0, 100.0, "", "", "", "Fidelity Brokerage"]]
