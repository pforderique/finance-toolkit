"""Tests for fidelity.src.preprocess against real Fidelity CSV exports.

Covers: header normalization across Title Case / sentence case exports, alias
application, filter ordering (disclaimers/blank lines/SPAXX/FDRXX vanish
silently), account resolution (Traditional IRA never warns because it's
prefix-filtered before account resolution), aggregation, and the two
data-loss protection sets.
"""

from pathlib import Path

import pytest

from fidelity.src import io_layer
from fidelity.src.preprocess import PreprocessError, preprocess_rows
from fidelity.src.settings import load_settings

DATA_DIR = Path(__file__).resolve().parents[2] / "fidelity" / "data"
AUG_CSV = Path("/Users/pfo/Downloads/Portfolio_Positions_Aug-10-2026.csv")

ALL_FILES = sorted(DATA_DIR.glob("*.csv"))
if AUG_CSV.exists():
    ALL_FILES = ALL_FILES + [AUG_CSV]

FIDELITY_DIR = Path(__file__).resolve().parents[2] / "fidelity"
PROJECT_SETTINGS_PATH = FIDELITY_DIR / "settings.toml"
EXAMPLE_SETTINGS_PATH = FIDELITY_DIR / "settings.example.toml"


@pytest.fixture
def settings():
    # settings.toml is gitignored (real account numbers), so fall back to the
    # tracked example rather than erroring out on a fresh clone.
    path = PROJECT_SETTINGS_PATH if PROJECT_SETTINGS_PATH.exists() else EXAMPLE_SETTINGS_PATH
    return load_settings(path)


@pytest.mark.parametrize("csv_path", ALL_FILES, ids=lambda p: p.name)
def test_parses_without_error(csv_path: Path, settings):
    raw_rows = io_layer.read_input_csv(csv_path)
    holdings, skipped_keys, observed_labels, warnings = preprocess_rows(raw_rows, settings)
    assert holdings, f"expected at least one holding parsed from {csv_path}"


@pytest.mark.parametrize("csv_path", ALL_FILES, ids=lambda p: p.name)
def test_traditional_ira_never_warns(csv_path: Path, settings):
    """Traditional IRA (000000003) is a mapped-but-disabled account that in every
    sample file holds only SPAXX/SPAXX**. The prefix filter must run BEFORE the
    account-mapping check, so it should never surface an 'unmapped account'
    warning even though the account is disabled."""

    raw_rows = io_layer.read_input_csv(csv_path)
    _, _, _, warnings = preprocess_rows(raw_rows, settings)
    for w in warnings:
        assert "000000003" not in w, f"unexpected warning about Traditional IRA: {w}"
        assert "Traditional IRA" not in w, f"unexpected warning about Traditional IRA: {w}"


@pytest.mark.parametrize("csv_path", ALL_FILES, ids=lambda p: p.name)
def test_no_warnings_at_all_for_clean_files(csv_path: Path, settings):
    """Disclaimers, blank lines, and money-market rows should vanish silently --
    zero warnings for any of the sample files (every account present is either
    mapped+enabled or filtered out before account resolution)."""

    raw_rows = io_layer.read_input_csv(csv_path)
    _, _, _, warnings = preprocess_rows(raw_rows, settings)
    assert warnings == [], f"unexpected warnings for {csv_path}: {warnings}"


@pytest.mark.parametrize("csv_path", ALL_FILES, ids=lambda p: p.name)
def test_no_omitted_symbols_leak_through(csv_path: Path, settings):
    raw_rows = io_layer.read_input_csv(csv_path)
    holdings, _, _, _ = preprocess_rows(raw_rows, settings)
    tickers = {h.ticker for h in holdings}
    assert not any(t.startswith("SPAXX") for t in tickers)
    assert not any(t.startswith("FDRXX") for t in tickers)
    assert "PENDING ACTIVITY" not in tickers


def test_aug_2026_cusip_symbols_pass_through_verbatim(settings):
    raw_rows = io_layer.read_input_csv(AUG_CSV)
    holdings, _, _, _ = preprocess_rows(raw_rows, settings)
    tickers = {h.ticker for h in holdings}
    assert "67080C105" in tickers
    assert "87281U480" in tickers
    assert "VSEQX" in tickers


def test_aug_2026_brkb_aliased_to_brk_dot_b(settings):
    raw_rows = io_layer.read_input_csv(AUG_CSV)
    holdings, _, _, _ = preprocess_rows(raw_rows, settings)
    by_key = {(h.ticker, h.account_label): h for h in holdings}
    assert ("BRK.B", "Fidelity Roth IRA") in by_key
    assert ("BRKB", "Fidelity Roth IRA") not in by_key


def test_aug_2026_observed_labels_excludes_disabled_traditional_ira(settings):
    raw_rows = io_layer.read_input_csv(AUG_CSV)
    _, _, observed_labels, _ = preprocess_rows(raw_rows, settings)
    # Traditional IRA maps to "Fidelity Brokerage" but disabled; its only holding
    # (SPAXX) is filtered before account resolution, so it can't even indirectly
    # contribute to observed_labels via that path.
    assert observed_labels <= settings.owned_labels()


def test_header_normalization_title_case_and_sentence_case(settings, tmp_path):
    """Both header stylings resolve to the same canonical fields."""
    title_case_rows = [
        {
            "Account Number": "111",
            "Account Name": "Individual",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "10",
            "Average Cost Basis": "100.00",
            "Cost Basis Total": "1000.00",
        }
    ]
    sentence_case_rows = [
        {
            "Account number": "111",
            "Account name": "Individual",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "10",
            "Average cost basis": "100.00",
            "Cost basis total": "1000.00",
        }
    ]

    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    h1, _, _, w1 = preprocess_rows(title_case_rows, local_settings)
    h2, _, _, w2 = preprocess_rows(sentence_case_rows, local_settings)

    assert w1 == [] and w2 == []
    assert len(h1) == 1 and len(h2) == 1
    assert h1[0].ticker == h2[0].ticker == "AAPL"
    assert h1[0].shares == h2[0].shares == 10.0
    assert h1[0].avg_cost == h2[0].avg_cost == 100.0


def test_missing_required_columns_raises(tmp_path):
    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    rows = [{"Symbol": "AAPL", "Quantity": "1"}]
    with pytest.raises(PreprocessError, match="missing required columns"):
        preprocess_rows(rows, local_settings)


def test_unparsable_quantity_is_protected_and_warns(tmp_path):
    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    rows = [
        {
            "Account Number": "111",
            "Account Name": "Individual",
            "Symbol": "AAPL",
            "Quantity": "not-a-number",
            "Average Cost Basis": "100.00",
            "Cost Basis Total": "1000.00",
        }
    ]
    holdings, skipped_keys, observed_labels, warnings = preprocess_rows(rows, local_settings)
    assert holdings == []
    assert ("AAPL", "Fidelity Brokerage") in skipped_keys
    assert "Fidelity Brokerage" in observed_labels
    assert len(warnings) == 1


def test_single_row_uses_average_cost_basis_verbatim_not_derived(tmp_path):
    """A single CSV row per key must use Fidelity's own `Average cost basis`
    verbatim, not cost_basis_total/quantity -- that division reintroduces
    precision Fidelity already rounded away (2dp), which would otherwise flag
    an unchanged sheet row as an update at spurious precision."""

    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    # Mirrors the live DHR/Fidelity Roth IRA row: 2 shares, Average cost basis
    # $207.36, but Cost basis total ($414.71) / 2 = 207.355 -- NOT 207.36.
    rows = [
        {
            "Account Number": "111",
            "Account Name": "Individual",
            "Symbol": "DHR",
            "Quantity": "2",
            "Average Cost Basis": "207.36",
            "Cost Basis Total": "414.71",
        }
    ]
    holdings, _, _, warnings = preprocess_rows(rows, local_settings)
    assert len(holdings) == 1
    assert holdings[0].avg_cost == 207.36
    assert not any("Aggregated" in w for w in warnings)


def test_dhr_roth_ira_two_shares_matches_sheet_and_is_unchanged(tmp_path):
    """Regression test: DHR / Fidelity Roth IRA at 2 shares / $207.36 avg cost
    (as in the live Aug-2026 CSV) must diff as UNCHANGED against a sheet row
    holding the identical values -- not flagged as an update."""

    from fidelity.src.datamodel import SheetRow
    from fidelity.src.workflow import plan_changes

    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "222"
name = "ROTH IRA"
label = "Fidelity Roth IRA"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    rows = [
        {
            "Account Number": "222",
            "Account Name": "ROTH IRA",
            "Symbol": "DHR",
            "Quantity": "2",
            "Average Cost Basis": "207.36",
            "Cost Basis Total": "414.71",
        }
    ]
    holdings, skipped_keys, observed_labels, _ = preprocess_rows(rows, local_settings)

    sheet_rows = [SheetRow("DHR", "Fidelity Roth IRA", 2.0, 207.36, row_number=134)]
    plan = plan_changes(sheet_rows, holdings, local_settings, observed_labels, skipped_keys)

    assert plan.updates == []
    assert len(plan.unchanged) == 1
    assert plan.unchanged[0].ticker == "DHR"


def test_duplicate_rows_are_aggregated(tmp_path):
    toml_text = """
[sheet]
spreadsheet_id = "S"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = true
"""
    path = tmp_path / "settings.toml"
    path.write_text(toml_text, encoding="utf-8")
    local_settings = load_settings(path)

    rows = [
        {
            "Account Number": "111",
            "Account Name": "Individual",
            "Symbol": "AAPL",
            "Quantity": "10",
            "Average Cost Basis": "100.00",
            "Cost Basis Total": "1000.00",
        },
        {
            "Account Number": "111",
            "Account Name": "Individual",
            "Symbol": "AAPL",
            "Quantity": "5",
            "Average Cost Basis": "200.00",
            "Cost Basis Total": "1000.00",
        },
    ]
    holdings, _, _, warnings = preprocess_rows(rows, local_settings)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.shares == 15.0
    assert h.avg_cost == pytest.approx(2000.0 / 15.0)
    assert any("Aggregated" in w for w in warnings)
