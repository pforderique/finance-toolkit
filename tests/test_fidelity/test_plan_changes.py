"""Unit tests for the pure diff engine `workflow.plan_changes`.

No I/O, no network -- hand-built SheetRow/HoldingRecord fixtures only.
"""

from pathlib import Path

import pytest

from fidelity.src.datamodel import HoldingRecord, SheetRow
from fidelity.src.settings import load_settings
from fidelity.src.workflow import plan_changes

SETTINGS_TOML = """
[sheet]
spreadsheet_id = "SHEET123"
tab = "Portfolio"
table = "INVESTMENT_HOLDINGS"

[symbols]
ignore_prefixes = ["SPAXX", "FDRXX"]
ignore_exact = ["PENDING ACTIVITY"]
aliases = { BRKB = "BRK.B" }

[tolerance]
shares = 1e-6
avg_cost = 0.005

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true

[[accounts]]
number = "222"
name = "ROTH IRA"
label = "Fidelity Roth IRA"
enabled = true

[[accounts]]
number = "333"
name = "Health Savings Account"
label = "Fidelity HSA"
enabled = true
"""


@pytest.fixture
def settings(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(SETTINGS_TOML, encoding="utf-8")
    return load_settings(path)


def test_partial_export_no_deletes_for_absent_accounts(settings):
    """Sheet has rows in both Fidelity Brokerage and Fidelity Roth IRA, but this
    CSV export only covered Fidelity Brokerage. The Roth IRA rows must NOT be
    deleted -- they were never observed in this CSV, so they're out of the
    delete scope (bug #2 in the plan)."""

    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 10.0, 100.0, row_number=7),
        SheetRow("MSFT", "Fidelity Roth IRA", 5.0, 200.0, row_number=8),
    ]
    holdings = [
        HoldingRecord("AAPL", "Fidelity Brokerage", 10.0, 100.0),
    ]
    observed_labels = {"Fidelity Brokerage"}  # Roth IRA never appeared in this CSV
    skipped_keys = set()

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, skipped_keys)

    assert plan.deletes == []
    assert len(plan.unchanged) == 1
    assert plan.unchanged[0].ticker == "AAPL"
    assert len(plan.untouched) == 1
    assert plan.untouched[0].ticker == "MSFT"


def test_unparsable_row_is_protected_not_deleted(settings):
    """A CSV row seen for an owned account but with unparsable numbers must be
    added to skipped_keys upstream, and the diff engine must NOT delete the
    corresponding sheet row (bug #1 in the plan)."""

    sheet_rows = [
        SheetRow("GME", "Fidelity Brokerage", 3.0, 20.0, row_number=7),
    ]
    holdings: list[HoldingRecord] = []  # no holding produced -- the row failed to parse
    observed_labels = {"Fidelity Brokerage"}
    skipped_keys = {("GME", "Fidelity Brokerage")}

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, skipped_keys)

    assert plan.deletes == []
    assert len(plan.unchanged) == 1
    assert plan.unchanged[0].ticker == "GME"


def test_unobserved_missing_holding_without_protection_still_no_delete(settings):
    """Sanity: if a label is simply never observed, missing holdings for it are
    untouched regardless of skipped_keys (scope guard alone is sufficient)."""

    sheet_rows = [
        SheetRow("GME", "Fidelity HSA", 3.0, 20.0, row_number=7),
    ]
    plan = plan_changes(sheet_rows, [], settings, observed_labels=set(), skipped_keys=set())
    assert plan.deletes == []
    assert len(plan.untouched) == 1


def test_duplicate_in_scope_sheet_row_produces_one_delete(settings):
    """Two sheet rows with the same (ticker, account_label) in scope: first wins,
    the extra becomes a DELETE plus a warning."""

    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 10.0, 100.0, row_number=7),
        SheetRow("AAPL", "Fidelity Brokerage", 10.0, 100.0, row_number=15),
    ]
    holdings = [HoldingRecord("AAPL", "Fidelity Brokerage", 10.0, 100.0)]
    observed_labels = {"Fidelity Brokerage"}

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, set())

    assert len(plan.deletes) == 1
    assert plan.deletes[0].row_number == 15
    assert len(plan.unchanged) == 1
    assert plan.unchanged[0].row_number == 7
    assert len(plan.warnings) == 1
    assert "Duplicate" in plan.warnings[0]


def test_sub_tolerance_drift_is_unchanged(settings):
    """Float noise below tolerance must NOT produce a phantom update."""

    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 10.0000001, 100.001, row_number=7),
    ]
    holdings = [HoldingRecord("AAPL", "Fidelity Brokerage", 10.0, 100.0)]
    observed_labels = {"Fidelity Brokerage"}

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, set())

    assert plan.updates == []
    assert len(plan.unchanged) == 1


def test_over_tolerance_drift_is_update(settings):
    sheet_rows = [
        SheetRow("AAPL", "Fidelity Brokerage", 10.0, 100.0, row_number=7),
    ]
    holdings = [HoldingRecord("AAPL", "Fidelity Brokerage", 11.0, 100.0)]
    observed_labels = {"Fidelity Brokerage"}

    plan = plan_changes(sheet_rows, holdings, settings, observed_labels, set())

    assert len(plan.updates) == 1
    assert plan.updates[0].new_shares == 11.0


def test_holding_with_no_sheet_row_is_add(settings):
    plan = plan_changes(
        sheet_rows=[],
        holdings=[HoldingRecord("NVDA", "Fidelity Brokerage", 1.0, 100.0)],
        settings=settings,
        observed_labels={"Fidelity Brokerage"},
        skipped_keys=set(),
    )
    assert len(plan.adds) == 1
    assert plan.adds[0].ticker == "NVDA"
    assert plan.adds[0].row_number is None


def test_out_of_scope_sheet_row_is_untouched_even_if_owned_label(settings):
    """A row whose label IS owned but wasn't observed in this CSV is untouched,
    not deleted -- covers the intersection semantics of in_scope()."""

    sheet_rows = [
        SheetRow("RH", "Fidelity HSA", 1.0, 1.0, row_number=7),
    ]
    plan = plan_changes(
        sheet_rows, holdings=[], settings=settings, observed_labels=set(), skipped_keys=set()
    )
    assert plan.deletes == []
    assert len(plan.untouched) == 1


def test_non_owned_label_row_is_untouched(settings):
    """Robinhood/Schwab/Google-401K-style rows: label not in settings.owned_labels()
    at all -> always untouched, never appear in adds/updates/deletes."""

    sheet_rows = [
        SheetRow("RH", "Robinhood Brokerage", 1.0, 1.0, row_number=7),
    ]
    plan = plan_changes(
        sheet_rows,
        holdings=[HoldingRecord("RH", "Robinhood Brokerage", 5.0, 5.0)],
        settings=settings,
        observed_labels={"Robinhood Brokerage"},
        skipped_keys=set(),
    )
    assert plan.adds == []
    assert plan.updates == []
    assert plan.deletes == []
    assert len(plan.untouched) == 1
