"""Unattended sync for the LaunchAgent job: dry run first, then a gated apply.

This is the only code path that writes to the sheet with nobody watching, so it
is deliberately more paranoid than `fidelity sync`. Every exit that is *not* a
clean apply sends an email and leaves the sheet untouched -- a skipped day costs
one manual `fidelity sync`, a bad unattended write costs a restore from the
`out/*_before.json` artifact.

Gates, in order:
  1. No CSV matching the glob in the watch dir      -> email, skip
  2. Newest CSV is older than `max_age_hours`       -> email, skip
  3. Dry run raises (auth, sheet shape, parse)      -> email, skip
  4. |net equity delta| > `max_net_equity_delta`    -> email, skip
  5. Plan is a no-op                                -> log, skip (no email)
  6. A pre-flight guard trips inside run_apply      -> email, skip

Mass deletes are never force-approved here: `allow_mass_delete` stays False, so
guard #4 of `run_apply` remains armed and a mass delete becomes case 6.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .settings import Settings
from . import workflow


@dataclass
class ScheduledResult:
    """What the run decided. `applied` is the only outcome that wrote."""

    status: str  # "applied" | "no_changes" | "skipped" | "error"
    reason: str
    csv_path: Optional[Path] = None
    counts: Optional[dict] = None
    net_equity_delta: Optional[float] = None
    emailed: bool = False

    @property
    def exit_code(self) -> int:
        # Only a hard error is a nonzero exit -- a deliberate skip is a
        # successful run of the guard, not a failure of the job.
        return 1 if self.status == "error" else 0


def find_latest_csv(watch_dir: Path, pattern: str) -> Optional[Path]:
    """Newest matching CSV by mtime, or None. mtime beats the filename date
    because a re-download of an old-dated file is still fresh data."""

    try:
        if not watch_dir.is_dir():
            return None
        matches: List[Path] = [p for p in watch_dir.glob(pattern) if p.is_file()]
        if not matches:
            return None
        return max(matches, key=lambda p: p.stat().st_mtime)
    except OSError:
        # macOS TCC denies ~/Downloads to processes without Full Disk Access.
        # Treat it as "no CSV" so the caller emails rather than stack-traces.
        return None


def _send(subject: str, body: str) -> bool:
    """Email via the toolkit's existing SMTP channel. Never raises."""

    try:
        from ms_screener.src.notify import send_plain_alert

        return send_plain_alert(subject=subject, body=body)
    except Exception:  # noqa: BLE001 - alerting must never mask the real outcome
        traceback.print_exc()
        return False


def _format_plan(csv_path: Path, counts: dict, delta: float) -> str:
    lines = [
        f"CSV:   {csv_path}",
        f"Adds:      {counts.get('adds', 0)}",
        f"Updates:   {counts.get('updates', 0)}",
        f"Deletes:   {counts.get('deletes', 0)}",
        f"Unchanged: {counts.get('unchanged', 0)}",
        f"Untouched: {counts.get('untouched', 0)}",
        f"Net equity delta: {delta:+,.2f}",
    ]
    return "\n".join(lines)


def run_scheduled(
    settings: Settings,
    compact: bool = True,
    force: bool = False,
) -> ScheduledResult:
    """Run the gated unattended sync. `force` bypasses only the delta tripwire."""

    sched = settings.schedule
    watch_dir = sched.resolved_watch_dir()

    csv_path = find_latest_csv(watch_dir, sched.csv_glob)
    if csv_path is None:
        reason = f"No file matching {sched.csv_glob!r} in {watch_dir}"
        emailed = _send(
            "[fidelity] scheduled sync skipped - no CSV found",
            f"{reason}\n\nDownload a fresh Portfolio Positions export and the next "
            "run will pick it up automatically.",
        )
        return ScheduledResult("skipped", reason, emailed=emailed)

    age = datetime.now() - datetime.fromtimestamp(csv_path.stat().st_mtime)
    if age > timedelta(hours=sched.max_age_hours):
        hours = age.total_seconds() / 3600
        reason = f"Newest CSV is {hours:.1f}h old (limit {sched.max_age_hours:.0f}h)"
        emailed = _send(
            "[fidelity] scheduled sync skipped - stale CSV",
            f"{reason}\n\nFile: {csv_path}\n\nSyncing a stale export would write "
            "yesterday's share counts over today's. Download a fresh one.",
        )
        return ScheduledResult("skipped", reason, csv_path=csv_path, emailed=emailed)

    try:
        dry = workflow.run_dry_run(csv_path, settings, compact=compact)
    except Exception as exc:  # noqa: BLE001 - report, never crash silently
        reason = f"Dry run failed: {exc}"
        emailed = _send(
            "[fidelity] scheduled sync FAILED - dry run error",
            f"{reason}\n\nFile: {csv_path}\n\n{traceback.format_exc()}",
        )
        return ScheduledResult("error", reason, csv_path=csv_path, emailed=emailed)

    counts = dry.plan.counts()
    delta = dry.plan.net_equity_delta()

    if not any(counts.get(k, 0) for k in ("adds", "updates", "deletes")):
        return ScheduledResult(
            "no_changes", "Sheet already matches the CSV", csv_path=csv_path,
            counts=counts, net_equity_delta=delta,
        )

    if not force and abs(delta) > sched.max_net_equity_delta:
        reason = (
            f"Net equity delta {delta:+,.2f} exceeds the "
            f"{sched.max_net_equity_delta:,.0f} threshold"
        )
        emailed = _send(
            "[fidelity] scheduled sync HELD - large delta, nothing written",
            f"{reason}\n\nThe sheet was NOT modified.\n\n"
            f"{_format_plan(csv_path, counts, delta)}\n\n"
            "Review it yourself with:\n"
            f"  uv run python -m fidelity.main diff {csv_path}\n\n"
            "Then apply if it looks right:\n"
            f"  uv run python -m fidelity.main sync {csv_path}",
        )
        return ScheduledResult(
            "skipped", reason, csv_path=csv_path, counts=counts,
            net_equity_delta=delta, emailed=emailed,
        )

    try:
        workflow.run_apply(csv_path, settings, compact=compact, allow_mass_delete=False)
    except workflow.SyncGuardError as exc:
        reason = f"Pre-flight guard tripped: {exc}"
        emailed = _send(
            "[fidelity] scheduled sync HELD - guard tripped, nothing written",
            f"{reason}\n\nThe sheet was NOT modified.\n\n"
            f"{_format_plan(csv_path, counts, delta)}",
        )
        return ScheduledResult(
            "skipped", reason, csv_path=csv_path, counts=counts,
            net_equity_delta=delta, emailed=emailed,
        )
    except Exception as exc:  # noqa: BLE001
        reason = f"Apply failed: {exc}"
        emailed = _send(
            "[fidelity] scheduled sync FAILED - apply error",
            f"{reason}\n\nThe write may be partial -- check the newest "
            f"out/*_before.json artifact.\n\n{traceback.format_exc()}",
        )
        return ScheduledResult(
            "error", reason, csv_path=csv_path, counts=counts,
            net_equity_delta=delta, emailed=emailed,
        )

    return ScheduledResult(
        "applied", "Applied", csv_path=csv_path, counts=counts, net_equity_delta=delta,
    )
