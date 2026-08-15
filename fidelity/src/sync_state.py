"""Local (non-sheet) record of the last successfully-applied sync.

Persisted to a small JSON file next to `fidelity/data/` (see
`constants.SYNC_STATE_PATH`), never to the Google Sheet itself. Written only
after a real apply (`workflow.run_apply`) actually writes -- never for a
dry run or `diff`. Backs the `fidelity status` command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fidelity.src import constants


@dataclass(frozen=True)
class SyncState:
    timestamp: str  # ISO 8601, local timezone
    csv_name: str
    counts: Dict[str, int]
    net_equity_delta: float

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "csv_name": self.csv_name,
            "counts": dict(self.counts),
            "net_equity_delta": self.net_equity_delta,
        }


def write_sync_state(
    csv_name: str,
    counts: Dict[str, int],
    net_equity_delta: float,
    path: Optional[Path] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Persist the outcome of a real apply. Overwrites any prior state --
    only the most recent successful sync is kept."""
    target = Path(path) if path is not None else constants.SYNC_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().astimezone().isoformat()
    state = SyncState(timestamp=ts, csv_name=csv_name, counts=dict(counts), net_equity_delta=net_equity_delta)
    target.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return target


def read_sync_state(path: Optional[Path] = None) -> Optional[Dict]:
    """Return the last-persisted sync state dict, or None if the tool has
    never successfully synced (or the state file is missing/unreadable)."""
    target = Path(path) if path is not None else constants.SYNC_STATE_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
