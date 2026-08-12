"""I/O layer for reading/writing CSVs and Google Sheets."""

import csv
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from dotenv import find_dotenv, load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import Resource
from googleapiclient.discovery import build

from fidelity.src import constants
from fidelity.src.datamodel import SheetRow, TableInfo, TargetRow

load_dotenv(find_dotenv())

GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Column names the INVESTMENT_HOLDINGS table must expose, resolved BY NAME.
EXPECTED_TABLE_COLUMNS = (
    "Ticker",
    "Shares",
    "Avg_Cost",
    "Mkt_Price",
    "Total_Equity",
    "Pct_Gain",
    "Account",
)


def read_input_csv(path: Path) -> List[dict]:
    """Read a CSV file into a list of dictionaries."""
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"CSV file {path} contains no data rows")
    return rows


def _resolve_credentials_path() -> Optional[Path]:
    candidates = [
        os.getenv(constants.ENV_SERVICE_ACCOUNT_JSON),
        os.getenv(constants.ENV_CREDENTIALS_PATH),
    ]
    for candidate in candidates:
        if candidate:
            resolved = Path(candidate).expanduser()
            if resolved.exists():
                return resolved
    return None


def _load_service_account_credentials():
    """Load Google service account credentials from env configuration."""
    credentials_path = _resolve_credentials_path()
    if credentials_path is None:
        raise RuntimeError(
            "Service account credentials not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON "
            "or GOOGLE_APPLICATION_CREDENTIALS."
        )
    try:
        raw = json.loads(credentials_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - validation guard
        raise RuntimeError(f"Invalid JSON in credentials file: {credentials_path}") from exc
    return service_account.Credentials.from_service_account_info(raw, scopes=[GOOGLE_SHEETS_SCOPE])


@lru_cache(maxsize=1)
def _get_sheets_service() -> Resource:
    """Create a Google Sheets API service client using a cached service account."""
    credentials = _load_service_account_credentials()
    # cache_discovery avoids writing to ~/.cache which may be sandboxed
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _column_letter(index: int) -> str:
    """0-based column index -> A1 column letter(s). Only ever called with small indices here."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def resolve_table(spreadsheet_id: str, tab: str, table_name: str) -> TableInfo:
    """Resolve a native Sheets table BY NAME on a given tab. One read-only API call.

    Hard-errors (RuntimeError) if the tab or table is not found, or if the table
    is missing any of the expected column names -- never falls back to hardcoded
    column letters.
    """

    service = _get_sheets_service()
    try:
        response = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                ranges=[tab],
                fields=(
                    "sheets(properties(sheetId,title,gridProperties),"
                    "tables(tableId,name,range,columnProperties))"
                ),
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError(
            f"Failed to resolve table metadata for tab '{tab}': {exc}"
        ) from exc

    sheets = response.get("sheets", [])
    if not sheets:
        raise RuntimeError(f"Tab '{tab}' not found in spreadsheet {spreadsheet_id}")

    sheet = sheets[0]
    props = sheet.get("properties", {})
    sheet_id = props.get("sheetId")
    tables = sheet.get("tables", [])
    found_names = [t.get("name") for t in tables]

    table = next((t for t in tables if t.get("name") == table_name), None)
    if table is None:
        raise RuntimeError(
            f"Table '{table_name}' not found on tab '{tab}'. "
            f"Tables present: {found_names or '(none)'}"
        )

    grange = table.get("range", {})
    start_row_index = grange.get("startRowIndex")
    end_row_index = grange.get("endRowIndex")
    start_col_index = grange.get("startColumnIndex", 0)

    if start_row_index is None or end_row_index is None:
        raise RuntimeError(f"Table '{table_name}' has no resolvable row range")
    if start_col_index != 0:
        raise RuntimeError(
            f"Table '{table_name}' does not start at column A "
            f"(startColumnIndex={start_col_index}); this tool assumes column A."
        )

    header_row = start_row_index + 1
    first_data_row = header_row + 1
    last_data_row = end_row_index
    capacity = last_data_row - first_data_row + 1

    column_props = table.get("columnProperties", [])
    column_index_by_name: Dict[str, int] = {}
    for idx, col in enumerate(column_props):
        name = col.get("columnName")
        col_index = col.get("columnIndex", idx)  # API omits columnIndex for index 0
        if name:
            column_index_by_name[name] = col_index

    missing = [c for c in EXPECTED_TABLE_COLUMNS if c not in column_index_by_name]
    if missing:
        raise RuntimeError(
            f"Table '{table_name}' is missing expected column(s) {missing}. "
            f"Columns present: {sorted(column_index_by_name)}"
        )

    last_col_letter = _column_letter(max(column_index_by_name.values()))
    range_a1 = f"{tab}!A{first_data_row}:{last_col_letter}{last_data_row}"

    return TableInfo(
        sheet_id=sheet_id,
        table_id=str(table.get("tableId")),
        table_name=table_name,
        tab=tab,
        header_row=header_row,
        first_data_row=first_data_row,
        last_data_row=last_data_row,
        capacity=capacity,
        column_index_by_name=column_index_by_name,
        range_a1=range_a1,
    )


def read_raw_values(spreadsheet_id: str, range_a1: str) -> List[List]:
    """Generic UNFORMATTED_VALUE read of any range. One read-only API call.

    Used both to build `SheetRow`s (via `read_table_block`) and to capture the
    pre-write rollback snapshot (`out/<ts>_before.json`) -- the raw grid, not
    the parsed/typed view.
    """

    service = _get_sheets_service()
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError(f"Failed to read range '{range_a1}': {exc}") from exc

    return response.get("values", [])


def read_table_block(spreadsheet_id: str, table_info: TableInfo) -> List[SheetRow]:
    """Read the resolved table's data rows (UNFORMATTED_VALUE). One read-only API call.

    Rows that are entirely blank (no ticker, no account label) are dropped --
    they're just unused capacity, not holdings.
    """

    values = read_raw_values(spreadsheet_id, table_info.range_a1)
    ncols = max(table_info.column_index_by_name.values()) + 1
    ticker_idx = table_info.column_index_by_name["Ticker"]
    shares_idx = table_info.column_index_by_name["Shares"]
    avg_cost_idx = table_info.column_index_by_name["Avg_Cost"]
    account_idx = table_info.column_index_by_name["Account"]

    rows: List[SheetRow] = []
    for offset, raw_row in enumerate(values):
        padded = list(raw_row) + [""] * (ncols - len(raw_row))
        ticker = str(padded[ticker_idx]).strip()
        account_label = str(padded[account_idx]).strip()
        if not ticker and not account_label:
            continue  # unused capacity slot

        rows.append(
            SheetRow(
                ticker=ticker.upper(),
                account_label=account_label,
                shares=_coerce_float(padded[shares_idx]),
                avg_cost=_coerce_float(padded[avg_cost_idx]),
                row_number=table_info.first_data_row + offset,
            )
        )

    return rows


def _coerce_float(value) -> Optional[float]:
    try:
        cleaned = str(value).replace(",", "").replace("$", "").strip()
        if cleaned == "":
            return None
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def build_write_request(table_info: TableInfo, target_rows: Sequence[TargetRow]) -> Dict:
    """Build the exact `values.batchUpdate` request body. Pure -- no I/O.

    This is THE safety-critical function in the tool: it is the only place
    that decides what gets written and where. Split out from `write_table_block`
    so the payload can be unit-tested directly (never mocking the network) --
    the regression guard on "only A:C and G, never D/E/F, never another tab,
    no structural request types" lives against this function's return value.

    Ranges are computed from the resolved `table_info`, never hardcoded.
    """

    ticker_idx = table_info.column_index_by_name["Ticker"]
    shares_idx = table_info.column_index_by_name["Shares"]
    avg_cost_idx = table_info.column_index_by_name["Avg_Cost"]
    account_idx = table_info.column_index_by_name["Account"]

    if (ticker_idx, shares_idx, avg_cost_idx) != (0, 1, 2):
        raise RuntimeError(
            "Unexpected column layout: expected Ticker/Shares/Avg_Cost at columns "
            f"A/B/C (indices 0/1/2), got indices {ticker_idx}/{shares_idx}/{avg_cost_idx}. "
            "Refusing to write -- this table doesn't match the layout this tool was built for."
        )

    abc_last_col = _column_letter(avg_cost_idx)
    account_col = _column_letter(account_idx)
    abc_range = f"{table_info.tab}!A{table_info.first_data_row}:{abc_last_col}{table_info.last_data_row}"
    account_range = (
        f"{table_info.tab}!{account_col}{table_info.first_data_row}:"
        f"{account_col}{table_info.last_data_row}"
    )

    abc_values = [
        [
            row.ticker,
            row.shares if row.shares is not None else "",
            row.avg_cost if row.avg_cost is not None else "",
        ]
        for row in target_rows
    ]
    account_values = [[row.account_label] for row in target_rows]

    return {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": abc_range, "values": abc_values},
            {"range": account_range, "values": account_values},
        ],
    }


def write_table_block(spreadsheet_id: str, table_info: TableInfo, target_rows: Sequence[TargetRow]) -> dict:
    """The ONLY Sheets write this tool ever issues: one `values.batchUpdate`
    call with exactly two data ranges (see `build_write_request`). Never a
    structural `spreadsheets.batchUpdate` -- no insertDimension, deleteDimension,
    sortRange, or table-shape request of any kind.
    """

    body = build_write_request(table_info, target_rows)
    service = _get_sheets_service()
    try:
        return (
            service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError(f"Sheets write failed: {exc}") from exc


def sheets_url_for(sheet_id: str) -> str:
    """Construct a Google Sheets URL for the given sheet ID."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def read_account_dropdown_labels(spreadsheet_id: str) -> List[str]:
    """Live-read the valid Account dropdown labels from `_Helper!B7:B`.

    The Portfolio!G (Account) column is a ONE_OF_RANGE dropdown sourced from
    `=_HELPER[Asset_Holdings]`, which lives in the `_Helper` tab's `_HELPER`
    native table, column B ("Asset_Holdings"), data starting row 7. Verified
    live against the sheet (header row 6, table startRowIndex=5).
    """
    service = _get_sheets_service()
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range="_Helper!B7:B",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError(
            "Failed to read Account dropdown labels from '_Helper!B7:B'. "
            "Ensure the sheet exists, the service account has access, and the "
            "Google Sheets API is enabled."
        ) from exc

    labels: List[str] = []
    for row in response.get("values", []):
        if row and str(row[0]).strip():
            labels.append(str(row[0]).strip())
    return labels
