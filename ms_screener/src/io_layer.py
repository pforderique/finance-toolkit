"""I/O layer for reading/writing CSVs and Google Sheets."""

import csv
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import Resource
from googleapiclient.discovery import build

load_dotenv()

GOOGLE_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _chunks(seq: List[str], size: int) -> List[List[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def _build_compare_url_base(perf_ids: List[str]) -> str:
    ids = ",".join(perf_ids)
    return f"https://research-morningstar-com.ezproxy.spl.org/compare?ids={ids}"


def read_csv_any(path: Path) -> List[dict]:
    """Read a CSV file into a list of dictionaries."""
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def write_csv(path: Path, rows: Iterable[dict], headers: Optional[Sequence[str]] = None) -> None:
    """Write a list of dictionaries to a CSV file."""
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not materialized:
        path.write_text("")
        return
    if headers is None:
        headers = list(materialized[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(materialized)


def _load_service_account_credentials():
    """Load Google service account credentials from env configuration."""
    # key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    json_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not json_path:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable is not set.")

    raw_json = Path(json_path).expanduser()
    if not raw_json.exists():
        raise RuntimeError(f"Service account file not found at {raw_json}. Check GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        info = json.load(raw_json.open())
    except json.JSONDecodeError as exc:  # pragma: no cover - validation guard
        raise RuntimeError(f"Invalid JSON in {raw_json}") from exc
    return service_account.Credentials.from_service_account_info(
        info, scopes=[GOOGLE_SHEETS_SCOPE]
    )

@lru_cache(maxsize=1)
def _get_sheets_service() -> Resource:
    """Create a Google Sheets API service client using a cached service account."""
    credentials = _load_service_account_credentials()
    # cache_discovery avoids writing to ~/.cache which may be sandboxed
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def read_collected_data_from_sheets(sheet_id: str, tab: str) -> List[dict]:
    """Read the collected data tab from Google Sheets into a list of dict rows."""

    service = _get_sheets_service()
    range_name = f"{tab}!A1:Z"

    try:
        response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=range_name)
            .execute()
        )
    except Exception as exc:  # pragma: no cover - network call
        message = (
            "Failed to read Google Sheet. Ensure the sheet exists, the service account email "
            "has access, and the Google Sheets API is enabled."
        )
        raise RuntimeError(message) from exc

    values: list[list[str]] = response.get("values", [])
    if not values:
        return []

    raw_headers = [header.strip() for header in values[0]]
    header_map = {
        "ticker": "Ticker",
        "perf_id": "Performance_ID",
        "performance_id": "Performance_ID",
        "uncertainty": "Uncertainty",
        "rating_last_updated": "Ratings_Date",
        "ratings_date": "Ratings_Date",
    }

    headers: List[str] = []
    for header in raw_headers:
        canonical = header_map.get(header.lower(), header)
        headers.append(canonical)
    rows: List[dict] = []
    for raw_row in values[1:]:
        if all(cell == "" for cell in raw_row):
            continue
        record: dict[str, Optional[str]] = {}
        for idx, key in enumerate(headers):
            cell_value = raw_row[idx] if idx < len(raw_row) else ""
            record[key] = cell_value.strip() if isinstance(cell_value, str) else cell_value
        rows.append(record)

    return rows


def sheets_url_for(sheet_id: Optional[str]) -> Optional[str]:
    """Construct a Google Sheets URL for the given sheet ID, if provided."""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}" if sheet_id else None


def _format_sheet_value(value):
    """Convert Python values into cell-friendly representations."""

    if value is None:
        return ""
    if isinstance(value, tuple) and len(value) == 2:
        old, new = value
        return f"{old or ''} -> {new or ''}".strip()
    if isinstance(value, (list, set, tuple)):
        return ", ".join(str(part) for part in value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return value
    return str(value)


def update_sheet(sheet_id: str, tab: str, rows: List[dict], headers: Optional[Sequence[str]] = None) -> None:
    """Replace the contents of a Google Sheets tab with the provided rows."""

    service = _get_sheets_service()
    body_values: List[List] = []

    resolved_headers: List[str] = list(headers or (list(rows[0].keys()) if rows else []))
    if resolved_headers:
        body_values.append(list(resolved_headers))

    for row in rows:
        body_values.append([_format_sheet_value(row.get(column)) for column in resolved_headers])

    range_name = f"{tab}!A1"

    try:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=range_name,
            body={},
        ).execute()

        if body_values:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": body_values},
            ).execute()
    except Exception as exc:  # pragma: no cover - network call
        detail = getattr(exc, "content", None)
        if detail and isinstance(detail, (bytes, bytearray)):
            try:
                payload = json.loads(detail.decode("utf-8"))
                error_message = payload.get("error", {}).get("message")
            # pylint: disable=broad-except
            except Exception:
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


def emit_compare_links(perf_ids: List[str], batch_size: int, out_dir: Path) -> Path:
    """Emit compare URLs for the given performance IDs, batching as needed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if batch_size <= 0:
        batches = [perf_ids] if perf_ids else []
    else:
        batches = _chunks(perf_ids, batch_size)
    links = [_build_compare_url_base(batch) for batch in batches if batch]
    target = out_dir / "compare_links.txt"
    target.write_text("\n".join(links), encoding="utf-8")
    return target


def discover_inputs(folder: Optional[Path], files: List[Path], default_dir: Path) -> List[Path]:
    """Discover input CSV files from the given folder or explicit files, defaulting to the data dir."""
    if files:
        return sorted({Path(f) for f in files if Path(f).exists()})

    base = folder or default_dir
    if not base.exists():
        return []

    results = []
    for ext in ("*.csv", "*.CSV"):
        results.extend(base.glob(ext))
    return sorted({Path(p) for p in results})


def fetch_collected_data(sheet_id: Optional[str], tab: str, data_dir: Path) -> Tuple[List[dict], List[str]]:
    """Fetch collected data from Google Sheets or local CSV fallback."""
    warnings: List[str] = []
    if sheet_id:
        collected = read_collected_data_from_sheets(sheet_id, tab)
    else:
        fallback = data_dir / f"{tab}.csv"
        if not fallback.exists():
            raise FileNotFoundError(
                f"Collected Data not found. Provide --sheet-id or create {fallback}"
            )
        collected = read_csv_any(fallback)
        warnings.append(f"Using local fallback: {fallback}")

    return collected, warnings


def read_sheet_as_dicts(sheet_id: str, tab: str) -> List[dict]:
    """Read an arbitrary sheet tab into a list of dictionaries."""

    service: Resource = _get_sheets_service()
    range_name = f"{tab}!A1:Z"

    try:
        response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=range_name)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        message = (
            "Failed to read Google Sheet tab. Ensure the tab exists and the service account "
            "has at least Viewer access."
        )
        raise RuntimeError(message) from exc

    values = response.get("values", [])
    if not values:
        return []

    headers = [header.strip() for header in values[0]]
    rows: List[dict] = []
    for raw_row in values[1:]:
        if all(cell == "" for cell in raw_row):
            continue
        record: dict[str, Optional[str]] = {}
        for idx, key in enumerate(headers):
            cell_value = raw_row[idx] if idx < len(raw_row) else ""
            record[key] = cell_value.strip() if isinstance(cell_value, str) else cell_value
        rows.append(record)

    return rows


def snapshot_path(out_dir: Path) -> Path:
    """Get the path to the snapshot CSV file, ensuring the directory exists."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "snapshot.csv"
