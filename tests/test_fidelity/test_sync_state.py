"""Unit tests for `sync_state` (local last-synced record) and the `status`
CLI command that reads it. No network/Sheets I/O involved."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from fidelity.main import app
from fidelity.src import constants, sync_state

runner = CliRunner()


def test_read_sync_state_never_synced_returns_none(tmp_path):
    missing = tmp_path / "sync_state.json"
    assert sync_state.read_sync_state(missing) is None


def test_write_then_read_round_trips(tmp_path):
    path = tmp_path / "sync_state.json"
    counts = {"adds": 1, "updates": 2, "deletes": 0, "unchanged": 5, "untouched": 3}

    written_path = sync_state.write_sync_state(
        csv_name="positions.csv",
        counts=counts,
        net_equity_delta=123.45,
        path=path,
        timestamp="2026-08-12T09:00:00-04:00",
    )

    assert written_path == path
    assert path.exists()

    state = sync_state.read_sync_state(path)
    assert state["csv_name"] == "positions.csv"
    assert state["counts"] == counts
    assert state["net_equity_delta"] == 123.45
    assert state["timestamp"] == "2026-08-12T09:00:00-04:00"


def test_write_sync_state_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "sync_state.json"
    sync_state.write_sync_state("positions.csv", {"adds": 0}, 0.0, path=path)
    assert path.exists()


def test_write_sync_state_overwrites_prior_state(tmp_path):
    path = tmp_path / "sync_state.json"
    sync_state.write_sync_state("first.csv", {"adds": 1}, 1.0, path=path, timestamp="t1")
    sync_state.write_sync_state("second.csv", {"adds": 2}, 2.0, path=path, timestamp="t2")

    state = sync_state.read_sync_state(path)
    assert state["csv_name"] == "second.csv"
    assert state["timestamp"] == "t2"


def test_read_sync_state_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "sync_state.json"
    path.write_text("not valid json", encoding="utf-8")
    assert sync_state.read_sync_state(path) is None


# ---------------------------------------------------------------------------
# `fidelity status` CLI command
# ---------------------------------------------------------------------------


def test_status_command_never_synced(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "SYNC_STATE_PATH", tmp_path / "sync_state.json")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Never synced" in result.stdout


def test_status_command_prints_last_sync(tmp_path, monkeypatch):
    state_path = tmp_path / "sync_state.json"
    monkeypatch.setattr(constants, "SYNC_STATE_PATH", state_path)

    sync_state.write_sync_state(
        csv_name="Portfolio_Positions_Aug-10-2026.csv",
        counts={"adds": 2, "updates": 1, "deletes": 0, "unchanged": 10, "untouched": 4},
        net_equity_delta=250.5,
        timestamp="2026-08-10T07:05:00-04:00",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Portfolio_Positions_Aug-10-2026.csv" in result.stdout
    assert "2 adds" in result.stdout
    assert "$250.50" in result.stdout
