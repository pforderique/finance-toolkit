"""Scraper for individual Morningstar stock pages to extract Uncertainty and Ratings_Date."""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
import pdfplumber
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ms_screener.src.logging_setup import console

BASE_URL = "https://research-morningstar-com.ezproxy.spl.org/quotes/{perf_id}"
TIER_THRESHOLDS = {1: 14, 2: 30, 3: 60}
PAGE_WAIT_SECONDS = 30

#: Wall-clock budget for Phase 1. When Morningstar goes slow, every ticker burns
#: all three retries and the run stretches past two hours — long enough that the
#: 07:45 morning brief reads a half-finished sheet. Tickers left over are
#: reported as pending and picked up by the next run instead.
PHASE1_BUDGET_SECONDS = 30 * 60


@dataclass
class IndividualScrapeResult:
    """Result of individual page scraping."""
    updated: list[dict]
    failed: list[str]
    skipped: list[str]
    pending: list[str]


def scrape_individual_pages(
    driver: Chrome,
    stocks: list[dict],
    max_stocks: int = 20,
    rate_limit_seconds: float = 3.0,
    download_dir: Optional[Path] = None,
    tickers: Optional[list[str]] = None,
    force_tickers: Optional[set[str]] = None,
    budget_seconds: Optional[float] = PHASE1_BUDGET_SECONDS,
) -> IndividualScrapeResult:
    """
    Two-phase scrape: first download+persist all PDFs, then extract from all persisted PDFs.

    Args:
        driver: Already-authenticated Chrome driver (caller owns lifecycle)
        stocks: List of dicts with: ticker, perf_id, ratings_date, uncertainty, moat
        max_stocks: Safety cap on number of stocks to scrape per run
        rate_limit_seconds: Seconds to wait between page requests
        download_dir: Temp directory for PDF downloads before persisting to artifacts/
        tickers: If provided, scrape only these tickers (bypasses staleness filter)
        budget_seconds: Wall-clock cap on Phase 1; leftovers become pending.
            None disables the cap.
    """
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "scrape_errors.log"

    logger = logging.getLogger("individual_scraper")
    if not logger.handlers:
        handler = logging.FileHandler(log_file)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    if tickers:
        ticker_set = {t.upper() for t in tickers}
        qualifying = [s for s in stocks if s.get("ticker", "").upper() in ticker_set]
        skipped = []
        console.print(f"[cyan]• Individual scrape: {len(qualifying)} pinned ticker(s)[/cyan]")
    else:
        qualifying, skipped = _filter_stale_stocks(stocks, force_tickers=force_tickers or set())
        fmv_forced = len(force_tickers & {s["ticker"].upper() for s in qualifying}) if force_tickers else 0
        console.print(
            f"[cyan]• {len(qualifying)} stocks qualify"
            + (f" ({fmv_forced} forced by FMV change)" if fmv_forced else "")
            + f", {len(skipped)} skipped (not stale)[/cyan]"
        )

    fresh_pdf_tickers: set[str] = set()
    pending = []
    if len(qualifying) > max_stocks:
        pending = [s["ticker"] for s in qualifying[max_stocks:]]
        qualifying = qualifying[:max_stocks]
        console.print(f"[yellow]• Capped to {max_stocks}; {len(pending)} pending next run[/yellow]")

    # ── Phase 1: Navigate each page, grab HTML data, download + persist PDF ──────
    console.rule("[bold]Phase 1: Downloading PDFs[/bold]")
    html_data: dict[str, dict] = {}
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    attempted: list[dict] = []

    for i, stock in enumerate(qualifying):
        if deadline and time.monotonic() > deadline:
            out_of_time = [s["ticker"] for s in qualifying[i:]]
            pending.extend(out_of_time)
            console.print(
                f"[yellow]• Phase 1 hit its {budget_seconds / 60:.0f}-minute budget;"
                f" {len(out_of_time)} ticker(s) deferred to next run[/yellow]"
            )
            logger.warning(
                f"Phase 1 budget exhausted after {len(attempted)} ticker(s); "
                f"deferred: {','.join(out_of_time)}"
            )
            break
        attempted.append(stock)

        time.sleep(rate_limit_seconds)
        ticker = stock["ticker"]
        perf_id = stock["perf_id"]

        try:
            driver.get(BASE_URL.format(perf_id=perf_id))
            WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            soup = BeautifulSoup(driver.page_source, "html.parser")
            html_data[ticker] = {
                "uncertainty": _extract_uncertainty(soup),
                "capital_allocation": _extract_capital_allocation(soup),
            }
        except Exception as exc:
            console.print(f"[red]• Navigation failed {ticker}: {exc}[/red]")
            logger.error(f"Navigation failed for {ticker}: {exc}")
            html_data[ticker] = {}
            continue

        if download_dir:
            pdf_path = _download_pdf_with_retry(driver, ticker, download_dir, logger)
            if pdf_path:
                _persist_pdf(pdf_path, ticker)
                fresh_pdf_tickers.add(ticker)
                console.print(f"[green]• PDF saved:  {ticker}[/green]")
            else:
                console.print(f"[yellow]• No PDF for {ticker} after retries[/yellow]")

    # Only tickers Phase 1 actually reached can be extracted; the rest are
    # pending, not failed, so they must not be reported as broken downloads.
    qualifying = attempted

    # ── Phase 2: Extract from persisted PDFs ─────────────────────────────────────
    console.rule("[bold]Phase 2: Extracting from PDFs[/bold]")
    updated = []
    failed = []

    for stock in qualifying:
        ticker = stock["ticker"]
        perf_id = stock["perf_id"]
        result: dict = {"ticker": ticker, "perf_id": perf_id}

        # A ticker only reaches Phase 2 because its data was judged stale, so a PDF
        # left over from an earlier run is stale by construction. Reading a date out
        # of it would republish a months-old date under a fresh last_scraped stamp —
        # exactly the silent-staleness bug. Refuse, and report the ticker as failed.
        if download_dir and ticker not in fresh_pdf_tickers:
            failed.append(ticker)
            console.print(
                f"[red]• {ticker}: PDF download failed — refusing to reuse stale"
                f" artifact for ratings_date[/red]"
            )
            logger.error(f"{ticker}: no fresh PDF this run; ratings_date not updated")
            continue

        existing_pdfs = sorted(ARTIFACTS_DIR.glob(f"{ticker}_*.pdf"), reverse=True)
        if existing_pdfs:
            pdf_result = _extract_from_pdf(existing_pdfs[0], ticker, perf_id)
            if pdf_result:
                if pdf_result.get("uncertainty"):
                    result["uncertainty"] = pdf_result["uncertainty"]
                if pdf_result.get("ratings_date"):
                    result["ratings_date"] = pdf_result["ratings_date"]
                if pdf_result.get("ratings_date_source"):
                    result["ratings_date_source"] = pdf_result["ratings_date_source"]
                if pdf_result.get("ratings_dates"):
                    result["ratings_dates"] = pdf_result["ratings_dates"]

        # Fill uncertainty gap from HTML SVG data (reliable fallback)
        h = html_data.get(ticker, {})
        if h.get("uncertainty") and not result.get("uncertainty"):
            result["uncertainty"] = h["uncertainty"]
        if h.get("capital_allocation"):
            result["capital_allocation"] = h["capital_allocation"]

        if result.get("uncertainty") or result.get("ratings_date"):
            updated.append(result)
            console.print(
                f"[green]• {ticker}:[/green]"
                f" uncertainty={result.get('uncertainty', '—')},"
                f" ratings_date={result.get('ratings_date', '—')}"
                f" ({result.get('ratings_date_source', 'unknown')})"
            )
            breakdown = result.get("ratings_dates") or {}
            if breakdown:
                parts = ", ".join(
                    f"{k}={v}" for k, v in sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)
                )
                console.print(f"[dim]    dates: {parts}[/dim]")
        else:
            failed.append(ticker)
            console.print(f"[red]• No data extracted for {ticker}[/red]")

    console.print(f"[cyan]• Skipped {len(skipped)} stocks (not stale)[/cyan]")
    if pending:
        console.print(f"[cyan]• Pending {len(pending)} stocks (hit cap, retry next run)[/cyan]")

    return IndividualScrapeResult(
        updated=updated,
        failed=failed,
        skipped=[s["ticker"] for s in skipped],
        pending=pending,
    )


def _assign_tier(moat: Optional[str], uncertainty: Optional[str]) -> int:
    """
    Assign staleness tier based on moat and uncertainty.
    Tier 1 (30 days): narrow/no moat or high/very high uncertainty
    Tier 2 (90 days): wide moat + medium uncertainty, or unknown
    Tier 3 (180 days): wide moat + low uncertainty
    """
    moat_lower = (moat or "").lower() if moat else None
    uncertainty_lower = (uncertainty or "").lower() if uncertainty else None

    if moat_lower == "wide":
        if uncertainty_lower == "low":
            return 3
        elif uncertainty_lower == "medium":
            return 2
        else:
            return 2 if uncertainty_lower is None else 1
    elif moat_lower in {"narrow", "none"}:
        return 1
    else:
        return 2


def _is_stale(ratings_date: Optional[str], tier: int) -> bool:
    """Check if ratings_date is stale based on tier threshold."""
    if not ratings_date or ratings_date.strip() == "":
        return True

    date_formats = ["%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y"]
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(ratings_date.strip(), fmt).date()
            days_old = (datetime.now().date() - parsed).days
            return days_old > TIER_THRESHOLDS[tier]
        except ValueError:
            continue

    return True


def _filter_stale_stocks(stocks: list[dict], force_tickers: set[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """Filter stocks into qualifying (stale/forced) and skipped (fresh) lists."""
    qualifying = []
    skipped = []

    for stock in stocks:
        if not stock.get("perf_id"):
            continue

        ticker = stock.get("ticker", "").upper()
        has_pdf = bool(list(ARTIFACTS_DIR.glob(f"{ticker}_*.pdf")))
        tier = _assign_tier(stock.get("moat"), stock.get("uncertainty"))
        if ticker in force_tickers or not has_pdf or _is_stale(stock.get("ratings_date"), tier):
            qualifying.append(stock)
        else:
            skipped.append(stock)

    return qualifying, skipped



def _extract_uncertainty(soup: BeautifulSoup) -> Optional[str]:
    """Extract Uncertainty value from page HTML (in SVG text elements)."""
    try:
        for svg in soup.find_all("svg"):
            tspans = svg.find_all("tspan")
            for i, tspan in enumerate(tspans):
                if tspan.get_text(strip=True).lower() == "uncertainty":
                    if i + 1 < len(tspans):
                        value = tspans[i + 1].get_text(strip=True)
                        if value in {"Low", "Medium", "High", "Very High", "Extreme"}:
                            return value
        return None
    except Exception as exc:
        console.print(f"[dim]Debug: Uncertainty parse failed: {exc}[/dim]")
        return None


def _extract_ratings_date(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract Ratings_Date from 'Published on' date in analysis header.

    Note: Availability varies by page; some stocks may not have this date in HTML.
    May require clicking 'Download Equity Report' button to expose PDF-based dates.
    """
    try:
        for span in soup.find_all("span"):
            if "date-section" not in (span.get("class") or []):
                continue
            text = span.get_text(strip=True)
            if "published on" in text.lower():
                parts = text.split("Published on")
                if len(parts) > 1:
                    date_str = parts[1].strip()
                    normalized = _normalize_date(date_str)
                    if normalized:
                        return normalized
        return None
    except Exception as exc:
        console.print(f"[dim]Debug: Ratings_Date parse failed: {exc}[/dim]")
        return None


def _normalize_date(date_str: str) -> Optional[str]:
    """Normalize date string to YYYY-MM-DD format."""
    date_str = date_str.strip()
    formats = [
        "%d %b %Y",    # "1 May 2026" or "30 Apr 2026"
        "%d %B %Y",    # "1 May 2026" with full month
        "%b %d, %Y",   # "Apr 30, 2026"
        "%B %d, %Y",   # "April 30, 2026"
        "%m/%d/%Y",    # "04/30/2026"
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(date_str, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _extract_capital_allocation(soup: BeautifulSoup) -> Optional[str]:
    """Extract Capital Allocation value if available (nice to have)."""
    try:
        for cell in soup.find_all("td"):
            if cell.get_text(strip=True).lower() == "capital allocation":
                next_cell = cell.find_next("td")
                if next_cell:
                    value = next_cell.get_text(strip=True)
                    if value in {"Exemplary", "Standard", "Poor"}:
                        return value
        return None
    except Exception:
        return None


ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts"


def _persist_pdf(pdf_path: Path, ticker: str) -> None:
    """Move downloaded PDF to artifacts/TICKER_DD_MM_YYYY.pdf, replacing any prior copy."""
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%d_%m_%Y")
    dest = ARTIFACTS_DIR / f"{ticker}_{date_str}.pdf"
    # Remove any existing PDF for this ticker (regardless of date)
    for old in ARTIFACTS_DIR.glob(f"{ticker}_*.pdf"):
        old.unlink(missing_ok=True)
    pdf_path.rename(dest)


def _download_pdf_with_retry(
    driver: Chrome, ticker: str, download_dir: Path, logger: logging.Logger
) -> Optional[Path]:
    """Try to download PDF with up to 3 attempts using escalating fallback strategies."""
    for attempt in range(3):
        try:
            before = {p.name for p in download_dir.iterdir()}
            if attempt == 0:
                _click_download_report_button(driver)
            elif attempt == 1:
                # Fallback: scroll into view + JS click (bypasses intercept issues)
                _click_download_report_button_js(driver)
            else:
                # Last resort: reload page, wait for it to settle, then normal click
                driver.refresh()
                WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(3)
                _click_download_report_button(driver)

            pdf_path = _wait_for_pdf_download(download_dir, before, timeout=60)
            if pdf_path:
                return pdf_path

            logger.warning(f"PDF timeout for {ticker}, attempt {attempt + 1}/3")
        except Exception as exc:
            logger.warning(f"PDF attempt {attempt + 1}/3 failed for {ticker}: {exc}")

        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    return None


#: The popover swallows its first click. Measured against a live page: click 1
#: leaves the menu closed, click 2 opens it, click 3 closes it again. The
#: original single-click code therefore failed on every ticker of every run and
#: only worked because the *next* retry landed the second click — a 30s+ tax per
#: ticker that turned slow Morningstar days into two-hour runs.
_POPOVER_CLICK_TRIES = 3
_POPOVER_SETTLE_SECONDS = 5

_POPOVER_XPATH = "//button[contains(@aria-label, 'Download Report')]"
_EQUITY_CSS = 'button[aria-label="Download Equity Report"]'


def _open_popover_and_click_equity(driver: Chrome, popover_btn, click) -> None:
    """Click the popover until the equity-report entry shows, then click it.

    `click` performs one click on an element (a plain Selenium click or a JS
    click), so both strategies share the retry loop.
    """
    for press in range(_POPOVER_CLICK_TRIES):
        click(popover_btn)
        try:
            equity_btn = WebDriverWait(driver, _POPOVER_SETTLE_SECONDS).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _EQUITY_CSS))
            )
        except TimeoutException:
            continue
        click(equity_btn)
        time.sleep(1)
        return

    raise TimeoutException(
        f"popover never revealed the equity report after "
        f"{_POPOVER_CLICK_TRIES} clicks"
    )


def _click_download_report_button(driver: Chrome) -> None:
    """Click 'Download Report(s)' to open popover, then click 'Download Equity Report'."""
    popover_btn = WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.element_to_be_clickable((By.XPATH, _POPOVER_XPATH))
    )
    _open_popover_and_click_equity(driver, popover_btn, lambda el: el.click())


def _click_download_report_button_js(driver: Chrome) -> None:
    """JS-click fallback: scroll into view then fire click via JS (bypasses intercept)."""
    popover_btn = WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.presence_of_element_located((By.XPATH, _POPOVER_XPATH))
    )
    driver.execute_script("arguments[0].scrollIntoView(true);", popover_btn)
    _open_popover_and_click_equity(
        driver, popover_btn,
        lambda el: driver.execute_script("arguments[0].click();", el),
    )


def _wait_for_pdf_download(download_dir: Path, before: set[str], timeout: int = 60) -> Optional[Path]:
    """Poll download directory for a new fully-downloaded PDF file."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in download_dir.iterdir():
            if path.name in before or path.suffix == ".crdownload":
                continue
            if path.suffix == ".pdf":
                time.sleep(0.5)
                return path
        time.sleep(1)
    return None


def _extract_from_pdf(pdf_path: Path, ticker: str, perf_id: str) -> Optional[dict]:
    """Extract Uncertainty and Ratings_Date from PDF using pdfplumber."""
    result = {"ticker": ticker, "perf_id": perf_id}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None

            all_text = ""
            for page in pdf.pages:
                all_text += page.extract_text() or ""

            uncertainty = _parse_pdf_uncertainty(all_text)
            if uncertainty:
                result["uncertainty"] = uncertainty

            all_dates = _parse_pdf_dates(all_text)
            if all_dates:
                result["ratings_date"] = max(all_dates.values())
                # Which source won — the reader needs to know whether the date
                # means "analyst reconfirmed FMV" or "analyst wrote something".
                result["ratings_date_source"] = _pick_date_source(all_dates)
                # Full per-source breakdown, kept for diagnostics only.
                result["ratings_dates"] = all_dates

        return result if len(result) > 2 else None
    except Exception:
        return None


def _parse_pdf_uncertainty(text: str) -> Optional[str]:
    """Extract Uncertainty value from PDF text."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "uncertainty" in line.lower():
            for j in range(i, min(i + 3, len(lines))):
                match = re.search(r"\b(Low|Medium|High|Very High|Extreme)\b", lines[j], re.IGNORECASE)
                if match:
                    return match.group(1)
    return None


# Sections whose Contents-table entry carries the date the analyst last wrote it.
_CONTENTS_SECTIONS = (
    "Analyst Note",
    "Business Strategy & Outlook",
    "Business Strategy and Outlook",
    "Economic Moat",
    "Fair Value and Profit Drivers",
    "Risk and Uncertainty",
    "Capital Allocation",
    "Bulls Say",
    "Bears Say",
)

# Two date spellings appear in the reports: "16 Jul 2026" and "Jul 16, 2026".
_DATE_ALT = r"(?:\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})"

# Dates deliberately EXCLUDED from the freshness signal:
#   "Report as of ..."        → when this PDF was generated (always ~today)
#   "Total Return % as of"    → price data refresh (always ~today)
#   "Last Close as of"        → price data refresh (always ~today)
#   "Morningstar Rating QQQ…" → nightly quant star recompute (always ~today)
#   "Assessment Undervalued…" → nightly quant recompute (always ~today)
# Including any of these would mark every stock "fresh" and destroy the signal.


def _parse_pdf_dates(text: str) -> dict[str, str]:
    """
    Collect every analyst-authored date in the report, keyed by source label.

    Only dates belonging to the SUBJECT company are collected. Morningstar reports
    embed peer-comparison panels that repeat "Fair Value as of <date>" for competing
    tickers; those live past page 1, so subject-level "as of" dates are read from the
    page-1 header block only. Contents/byline section dates are unambiguous and are
    collected from the whole document (the Analyst Notes Archive can hold newer ones).
    """
    dates: dict[str, str] = {}

    def _record(label: str, raw: str) -> None:
        normalized = _normalize_date(raw)
        if not normalized:
            return
        # Keep the newest value seen for a given label (e.g. archive analyst notes).
        if label not in dates or normalized > dates[label]:
            dates[label] = normalized

    sec_alt = "|".join(re.escape(s) for s in _CONTENTS_SECTIONS)

    # Contents-table entries: "Analyst Note (16 Jul 2026)"
    for m in re.finditer(rf"({sec_alt})\s*(?:/\s*Bears Say\s*)?\(({_DATE_ALT})\)", text, re.IGNORECASE):
        _record(m.group(1).lower(), m.group(2))

    # Body bylines: "Analyst Note Phelix Lee, Senior Equity Analyst, 16 Jul 2026"
    for m in re.finditer(rf"({sec_alt})\s+[^\n]{{0,90}}?,\s*({_DATE_ALT})", text, re.IGNORECASE):
        _record(m.group(1).lower(), m.group(2))

    # Subject-company "as of" dates — page-1 header block only, first occurrence wins.
    head = text[:4000]
    for label, pattern in (
        ("fair_value_as_of", rf"Fair Value as of\s+({_DATE_ALT})"),
        ("valuation_as_of", rf"Valuation as of\s+({_DATE_ALT})"),
    ):
        m = re.search(pattern, head, re.IGNORECASE)
        if m:
            _record(label, m.group(1))

    return dates


# How each raw date label reads in the brief. Two buckets matter to the reader:
# an analyst reconfirmed/changed the fair value, or an analyst wrote prose
# without necessarily touching the FMV.
_SOURCE_LABEL = {
    "fair_value_as_of": "FMV confirmed",
    "valuation_as_of": "FMV confirmed",
    "analyst note": "analyst note",
    "business strategy & outlook": "strategy section",
    "business strategy and outlook": "strategy section",
    "economic moat": "moat section",
    "fair value and profit drivers": "FMV writeup",
    "risk and uncertainty": "risk section",
    "capital allocation": "capital allocation",
    "bulls say": "bulls/bears",
    "bears say": "bulls/bears",
}

# When several sources share the winning date, report the most meaningful one.
_SOURCE_RANK = (
    "analyst note",
    "fair_value_as_of",
    "valuation_as_of",
    "fair value and profit drivers",
    "economic moat",
    "business strategy & outlook",
    "business strategy and outlook",
    "risk and uncertainty",
    "capital allocation",
    "bulls say",
    "bears say",
)


def _pick_date_source(dates: dict) -> Optional[str]:
    """Human-readable label for whichever source produced the winning date."""
    if not dates:
        return None
    newest = max(dates.values())
    winners = [k for k, v in dates.items() if v == newest]
    for key in _SOURCE_RANK:
        if key in winners:
            return _SOURCE_LABEL.get(key, key)
    return _SOURCE_LABEL.get(winners[0], winners[0])


def _parse_pdf_ratings_date(text: str) -> Optional[str]:
    """
    Most recent analyst activity date in the report (max across all sources).

    Any analyst touch counts as freshness: a new analyst note, a rewritten moat
    section, or a reconfirmed fair value. Taking the max means a stock is only
    "stale" when no analyst has touched any part of the report.
    """
    dates = _parse_pdf_dates(text)
    return max(dates.values()) if dates else None
