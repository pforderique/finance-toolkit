"""Unit tests for the Stage 3 apply path: the target-block materializer and
the write-request payload builder.

The payload-inspection tests are the core safety regression guard: they
assert the generated `values.batchUpdate` body touches ONLY the resolved
A:C and G ranges, never names columns D/E/F, never names a tab other than
the resolved one, and contains no structural request types at all (there's
no `spreadsheets.batchUpdate` call anywhere in this module -- these tests
also just confirm the body shape doesn't smuggle one in via a "requests" key).
"""

from __future__ import annotations

import pytest

from fidelity.src.datamodel import ChangeEntry, ChangePlan, SheetRow, TableInfo
from fidelity.src.io_layer import build_write_request
from fidelity.src.workflow import SyncGuardError, build_target_block

COLUMN_INDEX_BY_NAME = {
    "Ticker": 0,
    "Shares": 1,
    "Avg_Cost": 2,
    "Mkt_Price": 3,
    "Total_Equity": 4,
    "Pct_Gain": 5,
    "Account": 6,
}


def make_table_info(capacity: int = 219, tab: str = "Portfolio") -> TableInfo:
    return TableInfo(
        sheet_id=417815611,
        table_id="749296102",
        table_name="INVESTMENT_HOLDINGS",
        tab=tab,
        header_row=6,
        first_data_row=7,
        last_data_row=6 + capacity,
        capacity=capacity,
        column_index_by_name=dict(COLUMN_INDEX_BY_NAME),
        range_a1=f"{tab}!A7:G{6 + capacity}",
    )


# ---------------------------------------------------------------------------
# build_target_block
# ---------------------------------------------------------------------------


def test_compaction_drops_deletes_and_shifts_survivors_up():
    table_info = make_table_info(capacity=5)
    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7),
        SheetRow("MSFT", "Fidelity Brokerage", 2.0, 200.0, row_number=8),
        SheetRow("NVDA", "Fidelity Brokerage", 3.0, 300.0, row_number=9),
    ]
    plan = ChangePlan(
        deletes=[
            ChangeEntry("delete", "MSFT", "Fidelity Brokerage", 8, 2.0, 200.0, None, None)
        ]
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=True)

    tickers = [r.ticker for r in target]
    assert tickers[:2] == ["AAPL", "NVDA"]  # MSFT gone, NVDA shifted up -- no gap
    assert tickers[2:] == ["", "", ""]  # padded to capacity


def test_compaction_applies_updates_in_place():
    table_info = make_table_info(capacity=2)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    plan = ChangePlan(
        updates=[ChangeEntry("update", "AAPL", "Fidelity Brokerage", 7, 1.0, 100.0, 5.0, 150.0)]
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=True)

    assert target[0].ticker == "AAPL"
    assert target[0].shares == 5.0
    assert target[0].avg_cost == 150.0


def test_compaction_appends_adds_sorted_by_label_then_ticker():
    table_info = make_table_info(capacity=4)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    plan = ChangePlan(
        adds=[
            ChangeEntry("add", "ZETA", "Fidelity Roth IRA", None, None, None, 1.0, 1.0),
            ChangeEntry("add", "ABC", "Fidelity Brokerage", None, None, None, 1.0, 1.0),
        ]
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=True)

    assert [r.ticker for r in target[:3]] == ["AAPL", "ABC", "ZETA"]


def test_compaction_preserves_untouched_rows_from_other_institutions():
    """Non-Fidelity rows (Robinhood etc.) must survive compaction untouched,
    just shifted up if a Fidelity row ahead of them was deleted."""
    table_info = make_table_info(capacity=3)
    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7),
        SheetRow("RH", "Robinhood Brokerage", 9.0, 9.0, row_number=8),
    ]
    plan = ChangePlan(
        deletes=[ChangeEntry("delete", "AAPL", "Fidelity Brokerage", 7, 1.0, 100.0, None, None)],
        untouched=[
            ChangeEntry("untouched", "RH", "Robinhood Brokerage", 8, 9.0, 9.0, 9.0, 9.0)
        ],
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=True)

    assert target[0].ticker == "RH"
    assert target[0].account_label == "Robinhood Brokerage"
    assert target[0].shares == 9.0


def test_capacity_exceeded_raises_guard_error():
    table_info = make_table_info(capacity=1)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    plan = ChangePlan(
        adds=[ChangeEntry("add", "NVDA", "Fidelity Brokerage", None, None, None, 1.0, 1.0)]
    )

    with pytest.raises(SyncGuardError, match="capacity|room for"):
        build_target_block(table_info, sheet_rows, plan, compact=True)


def test_no_compact_blanks_deletes_in_place_and_reuses_slot_for_add():
    table_info = make_table_info(capacity=3)
    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7),
        SheetRow("MSFT", "Fidelity Brokerage", 2.0, 200.0, row_number=8),
    ]
    plan = ChangePlan(
        deletes=[ChangeEntry("delete", "MSFT", "Fidelity Brokerage", 8, 2.0, 200.0, None, None)],
        adds=[ChangeEntry("add", "NVDA", "Fidelity Brokerage", None, None, None, 3.0, 3.0)],
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=False)

    # AAPL stays at its original slot (offset 0); MSFT's slot (offset 1) is
    # reused by the add instead of appending after AAPL at the end.
    assert target[0].ticker == "AAPL"
    assert target[1].ticker == "NVDA"
    assert target[2].ticker == ""  # never-used capacity slot, still blank


def test_no_compact_never_used_slot_available_for_add_without_any_delete():
    table_info = make_table_info(capacity=3)
    sheet_rows = [SheetRow("AAPL", "Fidelity Brokerage", 1.0, 100.0, row_number=7)]
    plan = ChangePlan(
        adds=[ChangeEntry("add", "NVDA", "Fidelity Brokerage", None, None, None, 3.0, 3.0)]
    )

    target = build_target_block(table_info, sheet_rows, plan, compact=False)

    assert target[0].ticker == "AAPL"
    assert target[1].ticker == "NVDA"
    assert target[2].ticker == ""


# ---------------------------------------------------------------------------
# build_write_request -- the core safety regression guard
# ---------------------------------------------------------------------------


def test_write_request_touches_only_two_expected_ranges():
    table_info = make_table_info(capacity=219, tab="Portfolio")
    target = build_target_block(table_info, [], ChangePlan(), compact=True)

    request = build_write_request(table_info, target)

    assert set(request.keys()) == {"valueInputOption", "data"}
    assert request["valueInputOption"] == "USER_ENTERED"
    assert len(request["data"]) == 2

    ranges = {entry["range"] for entry in request["data"]}
    assert ranges == {"Portfolio!A7:C225", "Portfolio!G7:G225"}


def test_write_request_never_names_columns_d_e_f():
    table_info = make_table_info(capacity=219, tab="Portfolio")
    target = build_target_block(table_info, [], ChangePlan(), compact=True)

    request = build_write_request(table_info, target)

    for entry in request["data"]:
        range_str = entry["range"]
        # The A1 range column-letter component must be exactly A:C or G, never
        # D, E, or F. Checked against the literal range strings, not by
        # counting columns of data (which would miss a mis-specified range).
        assert range_str in ("Portfolio!A7:C225", "Portfolio!G7:G225"), range_str
        for forbidden in ("!D", "!E", "!F", ":D", ":E", ":F"):
            assert forbidden not in range_str


def test_write_request_never_names_another_tab():
    table_info = make_table_info(capacity=219, tab="Portfolio")
    target = build_target_block(table_info, [], ChangePlan(), compact=True)

    request = build_write_request(table_info, target)

    for entry in request["data"]:
        assert entry["range"].startswith("Portfolio!")


def test_write_request_row_count_matches_capacity():
    table_info = make_table_info(capacity=219, tab="Portfolio")
    target = build_target_block(table_info, [], ChangePlan(), compact=True)

    request = build_write_request(table_info, target)

    abc = next(e for e in request["data"] if e["range"] == "Portfolio!A7:C225")
    g = next(e for e in request["data"] if e["range"] == "Portfolio!G7:G225")
    assert len(abc["values"]) == 219
    assert all(len(row) == 3 for row in abc["values"])
    assert len(g["values"]) == 219
    assert all(len(row) == 1 for row in g["values"])


def test_write_request_contains_no_structural_request_types():
    """Defense in depth: even though this codebase never constructs a
    `spreadsheets.batchUpdate` body, assert the values.batchUpdate body has no
    'requests' key and none of the structural request-type names anywhere in
    its JSON-serializable content."""
    import json

    table_info = make_table_info(capacity=219, tab="Portfolio")
    target = build_target_block(table_info, [], ChangePlan(), compact=True)
    request = build_write_request(table_info, target)

    assert "requests" not in request
    serialized = json.dumps(request)
    for forbidden in (
        "insertDimension",
        "deleteDimension",
        "sortRange",
        "AddTableRequest",
        "UpdateTableRequest",
        "DeleteTableRequest",
    ):
        assert forbidden not in serialized


def test_write_request_rejects_unexpected_column_layout():
    good_table_info = make_table_info(capacity=1, tab="Portfolio")
    target = build_target_block(good_table_info, [], ChangePlan(), compact=True)

    bad_columns = dict(COLUMN_INDEX_BY_NAME)
    bad_columns["Shares"] = 5  # simulate a corrupted/unexpected table layout
    bad_table_info = TableInfo(
        sheet_id=good_table_info.sheet_id,
        table_id=good_table_info.table_id,
        table_name=good_table_info.table_name,
        tab=good_table_info.tab,
        header_row=good_table_info.header_row,
        first_data_row=good_table_info.first_data_row,
        last_data_row=good_table_info.last_data_row,
        capacity=good_table_info.capacity,
        column_index_by_name=bad_columns,
        range_a1=good_table_info.range_a1,
    )

    with pytest.raises(RuntimeError, match="Unexpected column layout"):
        build_write_request(bad_table_info, target)
