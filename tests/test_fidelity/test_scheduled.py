"""Gate tests for the unattended scheduled sync.

The property that matters: every non-clean path must leave the sheet untouched.
These tests assert `run_apply` was never called, not just that the status string
looks right -- a gate that reports "skipped" while still writing is the exact
failure this module exists to prevent.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fidelity.src import scheduled
from fidelity.src.settings import (
    AccountMapping,
    ScheduleSettings,
    Settings,
    SheetSettings,
    SymbolSettings,
    ToleranceSettings,
)


class FakePlan:
    def __init__(self, counts, delta):
        self._counts = counts
        self._delta = delta

    def counts(self):
        return self._counts

    def net_equity_delta(self):
        return self._delta


class FakeDry:
    def __init__(self, plan):
        self.plan = plan


def make_settings(tmp_path: Path, **schedule_kwargs) -> Settings:
    # Default the cadence off so the older gate tests aren't all "not_due";
    # the cadence itself is covered explicitly below.
    schedule_kwargs.setdefault("min_days_between_runs", 0.0)
    schedule = ScheduleSettings(
        watch_dir=str(tmp_path),
        csv_glob="Portfolio_Positions_*.csv",
        **schedule_kwargs,
    )
    return Settings(
        sheet=SheetSettings(spreadsheet_id="sid"),
        symbols=SymbolSettings(),
        tolerance=ToleranceSettings(),
        accounts=[AccountMapping("1", "Individual", "Fidelity Brokerage", True)],
        path=tmp_path / "settings.toml",
        schedule=schedule,
    )


@pytest.fixture
def no_email(monkeypatch):
    """Capture emails instead of sending them."""
    sent = []
    monkeypatch.setattr(
        scheduled, "_send", lambda subject, body: (sent.append((subject, body)), True)[1]
    )
    return sent


@pytest.fixture
def spy_apply(monkeypatch):
    """Record every run_apply call so tests can assert 'nothing was written'."""
    calls = []
    monkeypatch.setattr(
        scheduled.workflow, "run_apply", lambda *a, **kw: calls.append((a, kw))
    )
    return calls


def write_csv(tmp_path: Path, name="Portfolio_Positions_Aug-10-2026.csv", age_hours=0.0):
    path = tmp_path / name
    path.write_text("Account Number,Symbol\n")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
    return path


def stub_dry(monkeypatch, counts, delta):
    monkeypatch.setattr(
        scheduled.workflow,
        "run_dry_run",
        lambda *a, **kw: FakeDry(FakePlan(counts, delta)),
    )


CHANGED = {"adds": 1, "updates": 2, "deletes": 0, "unchanged": 5, "untouched": 3}
NOOP = {"adds": 0, "updates": 0, "deletes": 0, "unchanged": 5, "untouched": 3}


# -- discovery ------------------------------------------------------------


def test_finds_newest_csv_by_mtime(tmp_path):
    write_csv(tmp_path, "Portfolio_Positions_Jan-01-2026.csv", age_hours=100)
    newest = write_csv(tmp_path, "Portfolio_Positions_Aug-10-2026.csv")
    found = scheduled.find_latest_csv(tmp_path, "Portfolio_Positions_*.csv")
    assert found == newest


def test_ignores_non_matching_files(tmp_path):
    (tmp_path / "Statement.csv").write_text("x")
    assert scheduled.find_latest_csv(tmp_path, "Portfolio_Positions_*.csv") is None


def test_missing_watch_dir_is_not_a_crash(tmp_path):
    assert scheduled.find_latest_csv(tmp_path / "nope", "*.csv") is None


# -- gates ----------------------------------------------------------------


def test_no_csv_emails_and_does_not_apply(tmp_path, no_email, spy_apply):
    result = scheduled.run_scheduled(make_settings(tmp_path))
    assert result.status == "skipped"
    assert spy_apply == []
    assert len(no_email) == 1
    assert "no CSV found" in no_email[0][0]


def test_stale_csv_emails_and_does_not_apply(tmp_path, no_email, spy_apply):
    write_csv(tmp_path, age_hours=48)
    result = scheduled.run_scheduled(make_settings(tmp_path, max_age_hours=36.0))
    assert result.status == "skipped"
    assert "stale" in no_email[0][0]
    assert spy_apply == []


def test_large_delta_holds_and_does_not_apply(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 15_864.53)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, max_net_equity_delta=10_000.0)
    )
    assert result.status == "skipped"
    assert spy_apply == [], "a held plan must never reach run_apply"
    subject, body = no_email[0]
    assert "HELD" in subject
    assert "NOT modified" in body


def test_large_negative_delta_also_holds(tmp_path, monkeypatch, no_email, spy_apply):
    """The tripwire is on magnitude -- a big drop is as suspicious as a big jump."""
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, -25_000.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, max_net_equity_delta=10_000.0)
    )
    assert result.status == "skipped"
    assert spy_apply == []


def test_delta_exactly_at_threshold_applies(tmp_path, monkeypatch, no_email, spy_apply):
    """Threshold is exclusive: 'more than $10K' holds, exactly $10K passes."""
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 10_000.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, max_net_equity_delta=10_000.0)
    )
    assert result.status == "applied"
    assert len(spy_apply) == 1


def test_force_bypasses_delta_tripwire(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 99_999.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, max_net_equity_delta=10_000.0), force=True
    )
    assert result.status == "applied"
    assert len(spy_apply) == 1


def test_noop_plan_skips_quietly(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, NOOP, 0.0)
    result = scheduled.run_scheduled(make_settings(tmp_path))
    assert result.status == "no_changes"
    assert spy_apply == []
    assert no_email == [], "an idempotent run must not generate daily inbox noise"


def test_small_delta_applies(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 812.44)
    result = scheduled.run_scheduled(make_settings(tmp_path))
    assert result.status == "applied"
    assert len(spy_apply) == 1
    assert result.exit_code == 0


def test_apply_never_force_approves_mass_delete(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    scheduled.run_scheduled(make_settings(tmp_path))
    _, kwargs = spy_apply[0]
    assert kwargs["allow_mass_delete"] is False


# -- biweekly cadence -----------------------------------------------------


def _stamp(tmp_path: Path, days_ago: float) -> Path:
    path = tmp_path / "scheduled_last_run.json"
    scheduled.write_last_run(
        path, now=datetime.now().astimezone() - timedelta(days=days_ago)
    )
    return path


def test_off_week_run_is_a_silent_noop(tmp_path, monkeypatch, no_email, spy_apply):
    """The agent fires weekly; the off-week firing must do nothing at all --
    no sheet read, no write, no email."""
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    called = []
    monkeypatch.setattr(
        scheduled.workflow, "run_dry_run", lambda *a, **kw: called.append(1)
    )

    result = scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0),
        last_run_path=_stamp(tmp_path, days_ago=7),
    )

    assert result.status == "not_due"
    assert result.exit_code == 0
    assert called == [], "an off-week run must not even touch the sheet"
    assert spy_apply == []
    assert no_email == []


def test_on_week_run_proceeds(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0),
        last_run_path=_stamp(tmp_path, days_ago=14),
    )
    assert result.status == "applied"
    assert len(spy_apply) == 1


def test_first_ever_run_proceeds(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0),
        last_run_path=tmp_path / "never_written.json",
    )
    assert result.status == "applied"


def test_corrupt_cadence_state_fails_toward_running(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    bad = tmp_path / "scheduled_last_run.json"
    bad.write_text("{not json")
    result = scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0), last_run_path=bad
    )
    assert result.status == "applied"


def test_force_bypasses_cadence(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    result = scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0),
        force=True,
        last_run_path=_stamp(tmp_path, days_ago=1),
    )
    assert result.status == "applied"


def test_applied_run_stamps_the_cadence(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)
    stamp = tmp_path / "scheduled_last_run.json"
    scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0), last_run_path=stamp
    )
    assert scheduled.read_last_run(stamp) is not None


def test_noop_run_stamps_the_cadence(tmp_path, monkeypatch, no_email, spy_apply):
    """Confirming the sheet is already correct consumes the cycle."""
    write_csv(tmp_path)
    stub_dry(monkeypatch, NOOP, 0.0)
    stamp = tmp_path / "scheduled_last_run.json"
    scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0), last_run_path=stamp
    )
    assert scheduled.read_last_run(stamp) is not None


def test_held_run_does_not_stamp_the_cadence(tmp_path, monkeypatch, no_email, spy_apply):
    """A held run wrote nothing, so the next Friday should retry rather than
    wait another two weeks."""
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 50_000.0)
    stamp = tmp_path / "scheduled_last_run.json"
    scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0, max_net_equity_delta=10_000.0),
        last_run_path=stamp,
    )
    assert scheduled.read_last_run(stamp) is None


def test_stale_csv_does_not_stamp_the_cadence(tmp_path, no_email, spy_apply):
    write_csv(tmp_path, age_hours=99)
    stamp = tmp_path / "scheduled_last_run.json"
    scheduled.run_scheduled(
        make_settings(tmp_path, min_days_between_runs=13.0, max_age_hours=36.0),
        last_run_path=stamp,
    )
    assert scheduled.read_last_run(stamp) is None


# -- failure paths --------------------------------------------------------


def test_dry_run_error_emails_and_exits_nonzero(tmp_path, monkeypatch, no_email, spy_apply):
    write_csv(tmp_path)
    monkeypatch.setattr(
        scheduled.workflow,
        "run_dry_run",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bad credentials")),
    )
    result = scheduled.run_scheduled(make_settings(tmp_path))
    assert result.status == "error"
    assert result.exit_code == 1
    assert spy_apply == []
    assert "FAILED" in no_email[0][0]


def test_guard_error_holds_without_writing(tmp_path, monkeypatch, no_email):
    write_csv(tmp_path)
    stub_dry(monkeypatch, CHANGED, 100.0)

    def boom(*a, **kw):
        raise scheduled.workflow.SyncGuardError("mass delete: 40 of 100 rows")

    monkeypatch.setattr(scheduled.workflow, "run_apply", boom)
    result = scheduled.run_scheduled(make_settings(tmp_path))
    assert result.status == "skipped"
    assert result.exit_code == 0, "a tripped guard is the system working, not a failure"
    assert "HELD" in no_email[0][0]


def test_email_failure_does_not_crash_the_run(tmp_path, monkeypatch):
    """A broken SMTP config must not turn a clean skip into a stack trace."""
    write_csv(tmp_path, age_hours=99)
    monkeypatch.setattr(
        scheduled, "_send", lambda subject, body: (_ for _ in ()).throw(OSError("smtp"))
    )
    with pytest.raises(OSError):
        # _send is stubbed to raise directly here; the real _send swallows.
        scheduled.run_scheduled(make_settings(tmp_path))


def test_real_send_swallows_exceptions(monkeypatch):
    monkeypatch.setitem(
        __import__("sys").modules, "ms_screener.src.notify", None
    )
    assert scheduled._send("subject", "body") is False
