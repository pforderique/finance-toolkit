"""End-to-end workflow orchestration for the Morningstar screener tool."""

import math
import shlex
from pathlib import Path
from typing import List

from rich.table import Table

from ms_screener.src import analytics
from ms_screener.src import auto_download
from ms_screener.src import io_layer
from ms_screener.src import transform
from ms_screener.src.datamodel import OutColumn
from ms_screener.src.config import RunConfig, RunResult
from ms_screener.src.logging_setup import console, print_header, print_warnings, timestamp


def _resolve_paths(raw_input_value: str) -> List[Path]:
    if not raw_input_value:
        console.print(
            "[yellow]No paths provided. Please drag and drop CSV files or a folder.[/yellow]")
        raise RuntimeError("No CSV inputs located")

    try:
        dropped_tokens = shlex.split(raw_input_value)
    except ValueError as exc:
        console.print(f"[yellow]Unable to parse dropped paths: {exc}[/yellow]")
        raise RuntimeError("Invalid drag-and-drop input") from exc

    if not dropped_tokens:
        console.print(
            "[yellow]No paths detected. Please drag and drop again.[/yellow]")
        raise RuntimeError("No CSV inputs located")

    candidate_paths = [Path(token).expanduser() for token in dropped_tokens]

    paths: List[Path] = []
    if len(candidate_paths) == 1 and candidate_paths[0].is_dir():
        input_dir = candidate_paths[0]
        paths = [p for p in input_dir.glob("*.csv") if p.is_file()]
        if not paths:
            console.print(
                f"[yellow]No CSV files found in {input_dir}."
                " Place Morningstar exports there and try again.[/yellow]"
            )
            raise RuntimeError("No CSV inputs located")
    else:
        missing = [str(p) for p in candidate_paths if not p.exists()]
        if missing:
            console.print(
                f"[yellow]Missing file(s): {', '.join(missing)}[/yellow]")
            raise RuntimeError("One or more CSV paths do not exist")

        non_files = [str(p) for p in candidate_paths if not p.is_file()]
        if non_files:
            console.print(
                f"[yellow]Non-file path(s) provided: {', '.join(non_files)}[/yellow]")
            raise RuntimeError(
                "Only CSV files can be dropped when not supplying a folder")

        non_csv = [str(p)
                   for p in candidate_paths if p.suffix.lower() not in {".csv"}]
        if non_csv:
            console.print(
                f"[yellow]Non-CSV file(s) provided: {', '.join(non_csv)}[/yellow]")
            raise RuntimeError("Only CSV files are supported")

        paths = candidate_paths
    return paths


def run_workflow(cfg: RunConfig) -> RunResult:
    """Run the end-to-end Morningstar workflow based on the provided configuration."""

    print_header(f"[bold cyan]M* Workflow[/bold cyan]  •  {timestamp()}")

    warnings: List[str] = []

    console.rule("[bold]Read Collected Data Tab[/bold]")
    raw_collected, collected_warnings = io_layer.fetch_collected_data(
        cfg.sheet_id, cfg.data_tab, cfg.data_dir)
    collected, normalization_warnings = transform.normalize_collected_data(
        raw_collected)
    warnings.extend(collected_warnings + normalization_warnings)

    perf_ids = transform.compare_ready_perf_ids(collected)
    links_path = io_layer.emit_compare_links(
        perf_ids, cfg.compare_batch_size, cfg.out_dir)
    if cfg.compare_batch_size:
        link_batches = math.ceil(
            len(perf_ids) / cfg.compare_batch_size) if perf_ids else 0
    else:
        link_batches = len(perf_ids)
    console.print("[dim]done.[/dim]")

    console.rule("[bold]Compare Links[/bold]")
    console.print(
        f"[green]• Compare links:[/green] {links_path}"
        f"  ([dim]{len(perf_ids)} IDs → {link_batches} link(s)[/dim])"
    )
    with open(links_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            console.print(
                f"=> [link={line.strip()}]Compare Link {idx + 1}[/link]")

    paths: List[Path]
    if cfg.files:
        paths = cfg.files
    elif cfg.folder:
        folder = cfg.folder.expanduser()
        if not folder.exists() or not folder.is_dir():
            raise RuntimeError(f"Provided folder does not exist: {folder}")
        paths = sorted(folder.glob("*.csv"))
        if not paths:
            raise RuntimeError(f"No CSV files found in folder {folder}")
    elif cfg.auto:
        console.rule("[bold]Auto Download[/bold]")
        try:
            paths = auto_download.download_compare_csvs(
                links_path, headless=cfg.auto_headless)
        except auto_download.AutoDownloadError as exc:
            raise RuntimeError(str(exc)) from exc
    else:
        console.rule("[bold]Drag & Drop CSVs[/bold]")
        console.print(
            "[cyan]You can now drag and drop the exported Morningstar compare CSV files into the terminal or file explorer window.[/cyan]\n"
            "Place the files in the './data' directory or specify their location with --files/--folder as needed.\n"
            "Press Enter after you have placed the files to continue..."
        )
        paths = _resolve_paths(input(
            "Drag and drop the CSV file(s) or a folder containing them here, then"
            " press Enter: "
        ).strip())

    all_ms_rows: List[dict] = []
    for path in paths:
        rows = transform.parse_mstar_csv(path)
        all_ms_rows.extend(rows)

    console.print(
        f"[green]• Parsed rows:[/green] {len(all_ms_rows)} from {len(paths)} file(s)")

    console.rule("[bold]Create Snapshot[/bold]")
    merged_rows = transform.merge_dedupe(all_ms_rows)
    snapshot_rows = transform.merge_with_collected_data(merged_rows, collected)
    snapshot = analytics.build_snapshot(snapshot_rows)

    def _price_change_sort_key(row: dict) -> float:
        value = row.get(OutColumn.PRICE_CHANGE)
        return value if isinstance(value, (int, float)) else -1.0

    snapshot.sort(key=_price_change_sort_key)

    console.rule("[bold]Outputs[/bold]")
    snapshot_csv_path = io_layer.snapshot_path(cfg.out_dir)

    # if snapshot_csv_path.exists():
    #     previous_snapshot_rows: list[dict] = io_layer.read_csv_any(snapshot_csv_path)
    #     fmv_changes = transform.detect_fmv_changes(
    #         previous_snapshot_rows, snapshot)
    #     fmv_changes_csv = cfg.out_dir / "fair_value_changes.csv"
    #     io_layer.write_csv(fmv_changes_csv, fmv_changes,
    #                     headers=transform.FMV_CHANGE_HEADERS)
    #    console.print(
    #        f"[green]• Fair value deltas logged:[/green] {len(fmv_changes)}")
    fmv_changes = []
    fmv_changes_csv = cfg.out_dir / "fair_value_changes.csv"

    snapshot_public_rows = [
        transform.snapshot_row_to_public_row(row) for row in snapshot
    ]
    io_layer.write_csv(snapshot_csv_path, snapshot_public_rows,
                       headers=analytics.SNAPSHOT_HEADERS)
    console.print(
        "[green]• Snapshot written:[/green]"
        f" {snapshot_csv_path} ({len(snapshot)} rows)"
    )

    console.rule("[bold]Updating Sheet[/bold]")
    sheets_link = io_layer.sheets_url_for(cfg.sheet_id)
    if cfg.sheet_id and not cfg.dry_run:
        sheet_rows = transform.snapshot_to_sheets_rows(snapshot)

        try:
            io_layer.update_sheet(
                cfg.sheet_id, cfg.snapshot_tab,
                sheet_rows, headers=analytics.SNAPSHOT_HEADERS
            )
        except RuntimeError as exc:
            warnings.append(f"Snapshot sheet update failed: {exc}")
        else:
            console.print(
                "[green]• Snapshot sheet updated:[/green]"
                f" {cfg.snapshot_tab} ({len(snapshot)} rows)"
            )

        try:
            io_layer.update_sheet(
                cfg.sheet_id,
                cfg.changes_tab,
                fmv_changes,
                headers=transform.FMV_CHANGE_HEADERS,
            )
        except RuntimeError as exc:
            warnings.append(f"Fair value tab update failed: {exc}")
        else:
            console.print(
                "[green]• Fair value tab updated:[/green]"
                f" {cfg.changes_tab} ({len(fmv_changes)} rows)"
            )

    print_warnings(warnings)

    console.rule("[bold]Summary[/bold]")
    summary = Table(show_header=False, box=None)
    summary.add_row("Files processed", str(len(paths)))
    summary.add_row("Rows ingested", str(len(all_ms_rows)))
    summary.add_row("Snapshot rows", str(len(snapshot)))
    summary.add_row("Fair value changes", str(len(fmv_changes)))
    summary.add_row("Compare links", str(links_path))
    summary.add_row("Snapshot CSV", str(snapshot_csv_path))
    summary.add_row("Fair value CSV", str(fmv_changes_csv))
    if sheets_link:
        summary.add_row(
            "Google Sheet", f'[bold bright_blue][link={sheets_link}]{sheets_link}[/link][/bold bright_blue]')
    console.print(summary)

    return RunResult(
        total_files=len(paths),
        rows_ingested=len(all_ms_rows),
        rows_snapshot=len(snapshot),
        rows_fmv_changes=len(fmv_changes),
        warnings=warnings,
        compare_links_path=links_path,
        snapshot_csv_path=snapshot_csv_path,
        fmv_changes_csv_path=fmv_changes_csv,
        sheets_url=sheets_link,
    )
