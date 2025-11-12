"""I/O layer for reading/writing CSVs and Google Sheets."""

import csv
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import Resource
from googleapiclient.discovery import build

from fidelity.src import constants
from fidelity.src.datamodel import SheetRow, TableState

load_dotenv()

GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_HYPERLINK_PATTERN = re.compile(r'=HYPERLINK\(".*?",\s*"(.*?)"\)', re.IGNORECASE)


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


def read_portfolio_table(sheet_id: str, tab: str) -> TableState:
    """Read the portfolio tracker table with both formulas and values."""

    service = _get_sheets_service()
    range_name = f"{tab}!A{constants.SHEET_RANGE_START_ROW}:G"

    try:
        formulas_response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=sheet_id,
                range=range_name,
                valueRenderOption="FORMULA",
            )
            .execute()
        )
        values_response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=sheet_id,
                range=range_name,
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        message = (
            "Failed to read Google Sheet. Ensure the sheet exists, the service account email "
            "has access, and the Google Sheets API is enabled."
        )
        raise RuntimeError(message) from exc

    formula_rows = _normalize_rows(formulas_response.get("values", []))
    value_rows = _normalize_rows(values_response.get("values", []))

    rows: List[SheetRow] = []
    for offset, formula_row in enumerate(formula_rows):
        row_index = constants.SHEET_RANGE_START_ROW + offset
        value_row = value_rows[offset] if offset < len(value_rows) else [""] * 7

        ticker = _extract_ticker(formula_row[0])
        account_label = formula_row[6].strip()
        shares = _coerce_float(value_row[1])
        avg_cost = _coerce_float(value_row[2])
        description = formula_row[3].strip() or None

        rows.append(
            SheetRow(
                ticker=ticker,
                account_label=account_label,
                shares=shares,
                avg_cost=avg_cost,
                description=description,
                raw_index=row_index,
            )
        )

    return TableState(
        rows=rows,
        start_row_index=constants.SHEET_RANGE_START_ROW,
        raw_values=formula_rows,
        value_rows=value_rows,
    )


def _normalize_rows(values: List[List[str]]) -> List[List[str]]:
    normalized: List[List[str]] = []
    for row in values:
        padded = list(row)
        if len(padded) < len(constants.SHEET_RANGE_COLUMNS):
            padded.extend([""] * (len(constants.SHEET_RANGE_COLUMNS) - len(padded)))
        normalized.append(padded[: len(constants.SHEET_RANGE_COLUMNS)])
    return normalized


def _extract_ticker(value: str) -> str:
    if not value:
        return ""
    match = _HYPERLINK_PATTERN.match(value)
    if match:
        return match.group(1).strip().upper()
    return value.strip().upper()


def _coerce_float(value) -> Optional[float]:
    try:
        cleaned = str(value).replace(",", "").replace("$", "").strip()
        if cleaned == "":
            return None
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def write_portfolio_table(sheet_id: str, tab: str, rows: List[List[str]]) -> None:
    """Replace the contents of the sheet range starting at A3 with provided rows."""

    service = _get_sheets_service()
    range_name = f"{tab}!A{constants.SHEET_RANGE_START_ROW}"

    try:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=f"{tab}!A{constants.SHEET_RANGE_START_ROW}:{constants.SHEET_RANGE_COLUMNS[-1]}",
            body={},
        ).execute()

        if rows:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ).execute()
    except Exception as exc:  # pragma: no cover - network call
        detail = getattr(exc, "content", None)
        if detail and isinstance(detail, (bytes, bytearray)):
            try:
                payload = json.loads(detail.decode("utf-8"))
                error_message = payload.get("error", {}).get("message")
            except Exception:  # pylint: disable=broad-except
                error_message = None
        elif hasattr(exc, "error_details"):
            error_message = str(exc.error_details)
        else:
            error_message = str(exc)

        base_message = (
            "Failed to update Google Sheet. Confirm the service account has Editor access, "
            "the tab exists, and the Sheets API is enabled."
        )
        if error_message:
            raise RuntimeError(f"{base_message} (Google error: {error_message})") from exc
        raise RuntimeError(base_message) from exc


def sort_portfolio_table(sheet_id: str, tab: str, row_count: int, descending: bool = True) -> None:
    """Sort the synced range by the equity column using Google Sheets so formulas relink."""

    if row_count <= 1:
        return

    service = _get_sheets_service()
    try:
        metadata = (
            service.spreadsheets()
            .get(
                spreadsheetId=sheet_id,
                fields="sheets(properties(sheetId,title))",
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError("Failed to load spreadsheet metadata for sorting") from exc

    sheet_props = None
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == tab:
            sheet_props = props
            break

    if not sheet_props:
        raise RuntimeError(f"Tab '{tab}' not found while attempting to sort the sheet")

    start_row_index = constants.SHEET_RANGE_START_ROW - 1
    equity_column_index = constants.SHEET_RANGE_COLUMNS.index("E")
    sort_order = "DESCENDING" if descending else "ASCENDING"

    sort_request = {
        "sortRange": {
            "range": {
                "sheetId": sheet_props.get("sheetId"),
                "startRowIndex": start_row_index,
                "endRowIndex": start_row_index + row_count,
                "startColumnIndex": 0,
                "endColumnIndex": len(constants.SHEET_RANGE_COLUMNS),
            },
            "sortSpecs": [
                {
                    "dimensionIndex": equity_column_index,
                    "sortOrder": sort_order,
                }
            ],
        }
    }

    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [sort_request]},
        ).execute()
    except Exception as exc:  # pragma: no cover - network call
        raise RuntimeError("Failed to sort Google Sheet after updating rows") from exc


def write_snapshot(
        path: Path, 
        headers: Sequence[str], 
        rows: Iterable[Sequence],
        rows_before_header: Optional[Sequence[str]] = None
    ) -> None:
    """Persist the pre-update snapshot to disk."""
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if rows_before_header:
            writer.writerow(list(rows_before_header))
        writer.writerow(list(headers))
        writer.writerows(materialized)


def write_change_log(path: Path, records: Iterable[dict]) -> None:
    """Persist the change log for auditing."""
    materialized = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(materialized, handle, indent=2)


def sheets_url_for(sheet_id: str) -> str:
    """Construct a Google Sheets URL for the given sheet ID."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
