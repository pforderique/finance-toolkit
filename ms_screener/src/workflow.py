"""End-to-end workflow orchestration for the Morningstar screener tool."""

import math
import os
import shlex
import tempfile
from pathlib import Path
from typing import List

from rich.table import Table

from ms_screener.src import analytics
from ms_screener.src import auto_download
from ms_screener.src import individual_scraper
from ms_screener.src import io_layer
from ms_screener.src import transform
from ms_screener.src.datamodel import InColumn, OutColumn
from ms_screener.src.config import RunConfig, RunResult
from ms_screener.src.logging_setup import console, print_header, print_warnings, timestamp, alert_fmv_change


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


def run_scrape_only(cfg: RunConfig) -> None:
    """Login and run individual page scraping only — no CSV download or snapshot update."""
    print_header(f"[bold cyan]M* Scrape Only[/bold cyan]  •  {timestamp()}")
    warnings: List[str] = []

    console.rule("[bold]Read Collected Data[/bold]")
    raw_collected, collected_warnings = io_layer.fetch_collected_data(
        cfg.sheet_id, cfg.data_tab, cfg.data_dir)
    collected, normalization_warnings = transform.normalize_collected_data(raw_collected)
    warnings.extend(collected_warnings + normalization_warnings)

    console.rule("[bold]Read Screener Tab[/bold]")
    try:
        screener_rows = io_layer.read_sheet_as_dicts(cfg.sheet_id, cfg.snapshot_tab)
        screener_by_ticker = {
            (r.get(OutColumn.TICKER) or "").upper(): r for r in screener_rows
        }
        console.print(f"[dim]• Screener loaded for {len(screener_by_ticker)} tickers[/dim]")
    except Exception:
        screener_by_ticker = {}
        console.print("[yellow]Warning: could not read Screener tab — staleness checks degraded[/yellow]")

    stocks = [
        {
            "ticker": row[InColumn.TICKER],
            "perf_id": row[InColumn.PERFORMANCE_ID],
            "ratings_date": screener_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.RATINGS_DATE),
            "uncertainty": screener_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.UNCERTAINTY),
            "moat": screener_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.MOAT),
        }
        for row in collected
        if row.get(InColumn.PERFORMANCE_ID)
    ]

    console.rule("[bold]Login[/bold]")
    username = os.getenv("SPL_BARCODE")
    pin = os.getenv("SPL_PIN")
    if not username or not pin:
        raise RuntimeError("SPL credentials missing: set SPL_BARCODE and SPL_PIN")

    download_dir = Path(tempfile.mkdtemp(prefix="ms_scrape_"))
    driver = auto_download.build_driver(download_dir, headless=cfg.auto_headless)
    try:
        auto_download.perform_login(driver, username, pin)

        console.rule("[bold]Individual Page Scrape[/bold]")
        scrape_result = individual_scraper.scrape_individual_pages(
            driver, stocks,
            max_stocks=cfg.scrape_max_stocks,
            rate_limit_seconds=cfg.scrape_rate_limit,
            download_dir=download_dir,
            tickers=cfg.scrape_tickers or None,
        )

        if scrape_result.updated and cfg.sheet_id:
            if not cfg.dry_run:
                patched = io_layer.patch_screener_rows(
                    cfg.sheet_id, cfg.snapshot_tab, scrape_result.updated)
                console.print(
                    f"[green]• Screener tab patched:[/green] {patched} cell(s) updated")
            else:
                console.print(
                    f"[yellow]• [DRY RUN] Would patch {len(scrape_result.updated)} rows"
                    f" in {cfg.snapshot_tab}[/yellow]"
                )
    except Exception as exc:
        warnings.append(f"Scrape-only failed: {exc}")
    finally:
        driver.quit()

    print_warnings(warnings)


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
    scrape_result = None
    driver = None
    download_dir = None

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
            if cfg.scrape_individual:
                username = os.getenv("SPL_BARCODE")
                pin = os.getenv("SPL_PIN")
                if not username or not pin:
                    raise RuntimeError("SPL credentials missing for individual scrape")

                download_dir = Path(tempfile.mkdtemp(prefix="ms_auto_"))
                driver = auto_download.build_driver(download_dir, headless=cfg.auto_headless)
                try:
                    auto_download.perform_login(driver, username, pin)
                    paths = auto_download.download_compare_csvs(
                        links_path, driver=driver, download_dir=download_dir)
                except auto_download.AutoDownloadError as exc:
                    raise RuntimeError(str(exc)) from exc
            else:
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

    # Read current snapshot from sheet BEFORE writing new one to detect FMV changes
    try:
        prev_snapshot = io_layer.read_sheet_as_dicts(cfg.sheet_id, cfg.snapshot_tab)
    except Exception:
        prev_snapshot = []

    # Preserve scraped uncertainty/ratings_date from previous run into new snapshot rows
    prev_by_ticker = {(r.get(OutColumn.TICKER) or "").upper(): r for r in prev_snapshot}
    for row in snapshot:
        ticker = (row.get(OutColumn.TICKER) or "").upper()
        prev = prev_by_ticker.get(ticker, {})
        if not row.get(OutColumn.UNCERTAINTY):
            row[OutColumn.UNCERTAINTY] = prev.get(OutColumn.UNCERTAINTY) or None
        if not row.get(OutColumn.RATINGS_DATE):
            row[OutColumn.RATINGS_DATE] = prev.get(OutColumn.RATINGS_DATE) or None

    # Detect changes in fair value
    fmv_changes = transform.detect_fmv_changes(prev_snapshot, snapshot)

    console.rule("[bold]Outputs[/bold]")
    snapshot_csv_path = io_layer.snapshot_path(cfg.out_dir)
    fmv_changes_csv = cfg.out_dir / "fmv_changes.csv"

    snapshot_public_rows = [
        transform.snapshot_row_to_public_row(row) for row in snapshot
    ]
    io_layer.write_csv(snapshot_csv_path, snapshot_public_rows,
                       headers=analytics.SNAPSHOT_HEADERS)
    console.print(
        "[green]• Snapshot written:[/green]"
        f" {snapshot_csv_path} ({len(snapshot)} rows)"
    )

    # Append to FMV history tab if changes exist
    if fmv_changes and not cfg.dry_run and cfg.sheet_id:
        try:
            io_layer.append_to_sheet(
                cfg.sheet_id,
                cfg.fmv_history_tab,
                fmv_changes,
                headers=transform.FMV_CHANGE_HEADERS
            )
        except RuntimeError as exc:
            warnings.append(f"FMV history append failed: {exc}")
        else:
            alert_fmv_change(len(fmv_changes), cfg.fmv_history_tab)
            console.print(
                "[green]• FMV history appended:[/green]"
                f" {cfg.fmv_history_tab} ({len(fmv_changes)} rows)"
            )
    elif fmv_changes and cfg.dry_run:
        alert_fmv_change(len(fmv_changes), cfg.fmv_history_tab, dry_run=True)
        console.print(
            "[yellow]• [DRY RUN] FMV history would append:[/yellow]"
            f" {cfg.fmv_history_tab} ({len(fmv_changes)} rows)"
        )

    # Write new snapshot to sheet
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

    # Write local FMV changes CSV (overwrite is fine for local)
    if fmv_changes:
        io_layer.write_csv(fmv_changes_csv, fmv_changes, headers=transform.FMV_CHANGE_HEADERS)

    # Individual page scraping (auto-triggers after batch when cfg.scrape_individual)
    if cfg.scrape_individual:
        # Create driver if not already open (e.g. --files/--folder mode)
        if driver is None:
            username = os.getenv("SPL_BARCODE")
            pin = os.getenv("SPL_PIN")
            if not username or not pin:
                warnings.append("SPL credentials missing — individual scrape skipped")
            else:
                download_dir = Path(tempfile.mkdtemp(prefix="ms_scrape_"))
                driver = auto_download.build_driver(download_dir, headless=cfg.auto_headless)
                try:
                    auto_download.perform_login(driver, username, pin)
                except Exception as exc:
                    warnings.append(f"Login failed for individual scrape: {exc}")
                    driver.quit()
                    driver = None

        if driver:
            console.rule("[bold]Individual Page Scrape[/bold]")
            stocks = [
                {
                    "ticker": row[InColumn.TICKER],
                    "perf_id": row[InColumn.PERFORMANCE_ID],
                    "ratings_date": prev_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.RATINGS_DATE),
                    "uncertainty": prev_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.UNCERTAINTY),
                    "moat": prev_by_ticker.get(row[InColumn.TICKER], {}).get(OutColumn.MOAT),
                }
                for row in collected
                if row.get(InColumn.PERFORMANCE_ID)
            ]
            try:
                fmv_changed_tickers = {
                    r.get(OutColumn.TICKER, "").upper()
                    for r in fmv_changes
                    if r.get(OutColumn.TICKER)
                }
                if fmv_changed_tickers:
                    console.print(
                        f"[cyan]• FMV-changed tickers forced into scrape: "
                        f"{', '.join(sorted(fmv_changed_tickers))}[/cyan]"
                    )
                scrape_result = individual_scraper.scrape_individual_pages(
                    driver, stocks,
                    max_stocks=cfg.scrape_max_stocks,
                    rate_limit_seconds=cfg.scrape_rate_limit,
                    download_dir=download_dir,
                    tickers=cfg.scrape_tickers or None,
                    force_tickers=fmv_changed_tickers,
                )
                if scrape_result.updated and cfg.sheet_id:
                    if not cfg.dry_run:
                        patched = io_layer.patch_screener_rows(
                            cfg.sheet_id, cfg.snapshot_tab, scrape_result.updated)
                        console.print(
                            f"[green]• Screener tab patched:[/green] {patched} cell(s) updated")
                    else:
                        console.print(
                            f"[yellow]• [DRY RUN] Would patch {len(scrape_result.updated)} rows"
                            f" in {cfg.snapshot_tab}[/yellow]"
                        )
            except Exception as exc:
                warnings.append(f"Individual page scraping failed: {exc}")
            finally:
                driver.quit()
                driver = None

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
