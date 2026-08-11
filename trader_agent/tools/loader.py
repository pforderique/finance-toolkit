"""Read Google Sheets tabs and return raw row dicts."""

import json
import os
import sys
import argparse

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from ms_screener.src.io_layer import read_sheet_as_dicts
from ms_screener.src.config import DEFAULT_SNAPSHOT_TAB, DEFAULT_FMV_HISTORY_TAB
from ms_screener.src.transform import read_data_change_rows


def load_screener(sheet_id: str = None, tab: str = DEFAULT_SNAPSHOT_TAB) -> list[dict]:
    """Read Screener tab. Returns raw row dicts."""
    if sheet_id is None:
        sheet_id = os.environ["SHEET_ID"]
    return read_sheet_as_dicts(sheet_id, tab)


def load_fmv_history(sheet_id: str = None, tab: str = DEFAULT_FMV_HISTORY_TAB) -> list[dict]:
    """Read Data_Changes tab.

    Uses the layout-aware reader so rows written under the old 10-column layout (and
    rows written while the sheet's header row was stale) still map onto the right
    column names.
    """
    if sheet_id is None:
        sheet_id = os.environ["SHEET_ID"]
    return read_data_change_rows(sheet_id, tab)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", choices=["screener", "fmv"], required=True)
    args = parser.parse_args()

    sheet_id = os.environ["SHEET_ID"]
    if args.tab == "screener":
        rows = load_screener(sheet_id)
    else:
        rows = load_fmv_history(sheet_id)

    json.dump(rows, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
