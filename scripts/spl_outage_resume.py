"""One-shot: re-enable the paused LaunchAgents after the SPL ILS outage.

SPL took its EZProxy auth offline Sept 6-15, 2026 for an ILS upgrade, so
ms_screener could not log in and trader_brief would only have emailed a red
staleness banner. Both agents were unloaded by hand. This script reloads them
on Sept 16 and says so by email, because a silently-resumed job is just as
easy to miss as a silently-dead one.

If SPL is still down, the email says how to pause again.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ms_screener.src.notify import send_plain_alert  # noqa: E402

AGENTS = ("com.pfo.ms_screener", "com.pfo.trader_brief")
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"


def _bootstrap(label: str) -> str:
    plist = AGENT_DIR / f"{label}.plist"
    if not plist.exists():
        return f"MISSING {plist}"
    proc = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return "reloaded"
    # Already loaded is a success for our purposes, not an error worth alarming on.
    err = (proc.stderr or proc.stdout).strip()
    return "already loaded" if "already" in err.lower() else f"FAILED ({err})"


def main() -> int:
    results = {label: _bootstrap(label) for label in AGENTS}
    lines = [f"    {label}: {status}" for label, status in results.items()]
    ok = all(s in ("reloaded", "already loaded") for s in results.values())

    body = f"""SPL outage window is over — scheduled jobs resumed.

{chr(10).join(lines)}

ms_screener runs weekdays 07:05, trader_brief 07:45. The first run will
re-scrape everything that went stale during the Sept 6-15 SPL ILS upgrade,
so expect it to take longer than usual.

If SPL EZProxy is still rejecting logins, pause them again with:

    launchctl bootout gui/$(id -u)/com.pfo.ms_screener
    launchctl bootout gui/$(id -u)/com.pfo.trader_brief

Check the pipeline:
    cd /Users/pfo/ws/finance/finance-toolkit
    uv run python -m trader_agent.tools.health
"""
    subject = "ms_screener + trader_brief resumed" if ok else "Failed to resume scheduled jobs"
    send_plain_alert(subject, body)
    print(body)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
