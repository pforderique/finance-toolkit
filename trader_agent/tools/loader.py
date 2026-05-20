"""Read Google Sheets tabs and return raw row dicts."""

import json
import os
import sys
import argparse

from ms_screener.src.io_layer import read_sheet_as_dicts
from ms_screener.src.config import DEFAULT_SNAPSHOT_TAB, DEFAULT_FMV_HISTORY_TAB


def load_screener(sheet_id: str = None, tab: str = DEFAULT_SNAPSHOT_TAB) -> list[dict]:
    """Read Screener tab. Returns raw row dicts."""
    if sheet_id is None:
        sheet_id = os.environ["GOOGLE_SHEET_ID"]
    return read_sheet_as_dicts(sheet_id, tab)


def load_fmv_history(sheet_id: str = None, tab: str = DEFAULT_FMV_HISTORY_TAB) -> list[dict]:
    """Read FMV_History tab. Returns raw row dicts."""
    if sheet_id is None:
        sheet_id = os.environ["GOOGLE_SHEET_ID"]
    return read_sheet_as_dicts(sheet_id, tab)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", choices=["screener", "fmv"], required=True)
    args = parser.parse_args()

    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    if args.tab == "screener":
        rows = load_screener(sheet_id)
    else:
        rows = load_fmv_history(sheet_id)

    json.dump(rows, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
