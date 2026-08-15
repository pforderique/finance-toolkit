"""Tests for fidelity.src.settings."""

from pathlib import Path

import pytest

from fidelity.src.settings import (
    AccountMapping,
    SettingsError,
    load_settings,
    save_settings,
)

VALID_TOML = """
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
"""


def _write(tmp_path: Path, text: str, name: str = "settings.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_settings_basic(tmp_path):
    path = _write(tmp_path, VALID_TOML)
    settings = load_settings(path)
    assert settings.sheet.spreadsheet_id == "SHEET123"
    assert len(settings.accounts) == 2
    assert settings.owned_labels() == {"Fidelity Brokerage", "Fidelity Roth IRA"}


def test_match_precedence_number_before_name(tmp_path):
    """When both a number match and a differently-numbered name match exist,
    the number match must win (Fidelity renames accounts over time, so the
    name on a given CSV row is not authoritative)."""
    toml_text = VALID_TOML + """
[[accounts]]
number = "333"
name = "Individual"
label = "Robinhood Brokerage"
enabled = true
"""
    path = _write(tmp_path, toml_text)
    settings = load_settings(path)

    # Row identifies itself by number "111" but its *name* collides with the
    # account mapped under number "333". Number must win -> Fidelity Brokerage.
    resolved = settings.resolve_label(number="111", name="Individual")
    assert resolved == "Fidelity Brokerage"

    # No number given (or unknown number) -> falls back to name match.
    resolved_by_name = settings.resolve_label(number="", name="individual")
    # First match by name wins in list order (account 111), case-insensitive.
    assert resolved_by_name == "Fidelity Brokerage"

    # Unknown number, name matching a different account -> that account's label.
    resolved_other = settings.resolve_label(number="999", name="ROTH IRA")
    assert resolved_other == "Fidelity Roth IRA"


def test_duplicate_number_rejected_on_load(tmp_path):
    toml_text = VALID_TOML + """
[[accounts]]
number = "111"
name = "Duplicate"
label = "Fidelity HSA"
enabled = true
"""
    path = _write(tmp_path, toml_text)
    with pytest.raises(SettingsError, match="duplicate account number"):
        load_settings(path)


def test_duplicate_number_rejected_on_add(tmp_path):
    path = _write(tmp_path, VALID_TOML)
    settings = load_settings(path)
    with pytest.raises(SettingsError, match="already exists"):
        settings.add_account(
            AccountMapping(number="111", name="Whatever", label="Fidelity HSA")
        )


def test_malformed_toml_gives_clean_error(tmp_path):
    path = _write(tmp_path, "this is [not valid toml", name="broken.toml")
    with pytest.raises(SettingsError, match=r"malformed TOML"):
        load_settings(path)


def test_missing_file_gives_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(SettingsError, match="not found"):
        load_settings(missing)


def test_no_enabled_accounts_rejected(tmp_path):
    toml_text = """
[sheet]
spreadsheet_id = "SHEET123"

[[accounts]]
number = "111"
name = "Individual"
label = "Fidelity Brokerage"
enabled = false
"""
    path = _write(tmp_path, toml_text)
    with pytest.raises(SettingsError, match="at least one account must be enabled"):
        load_settings(path)


def test_save_settings_round_trips_atomically(tmp_path):
    path = _write(tmp_path, VALID_TOML)
    settings = load_settings(path)

    settings.add_account(
        AccountMapping(number="999", name="New Acct", label="Fidelity HSA", enabled=True)
    )
    save_settings(settings)

    # No leftover temp files.
    leftovers = [p for p in tmp_path.iterdir() if p.name != "settings.toml"]
    assert leftovers == []

    reloaded = load_settings(path)
    assert reloaded.find_by_number("999") is not None
    assert len(reloaded.accounts) == 3

    reloaded.remove_account("999")
    save_settings(reloaded)

    final = load_settings(path)
    assert len(final.accounts) == 2
    assert final.find_by_number("999") is None
