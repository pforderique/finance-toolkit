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
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ms_screener.src.logging_setup import console

BASE_URL = "https://research-morningstar-com.ezproxy.spl.org/quotes/{perf_id}"
TIER_THRESHOLDS = {1: 14, 2: 30, 3: 60}
PAGE_WAIT_SECONDS = 30


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
        qualifying, skipped = _filter_stale_stocks(stocks)
        console.print(
            f"[cyan]• {len(qualifying)} stocks qualify, {len(skipped)} skipped (not stale)[/cyan]"
        )

    pending = []
    if len(qualifying) > max_stocks:
        pending = [s["ticker"] for s in qualifying[max_stocks:]]
        qualifying = qualifying[:max_stocks]
        console.print(f"[yellow]• Capped to {max_stocks}; {len(pending)} pending next run[/yellow]")

    # ── Phase 1: Navigate each page, grab HTML data, download + persist PDF ──────
    console.rule("[bold]Phase 1: Downloading PDFs[/bold]")
    html_data: dict[str, dict] = {}

    for stock in qualifying:
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
                console.print(f"[green]• PDF saved:  {ticker}[/green]")
            else:
                console.print(f"[yellow]• No PDF for {ticker} after retries[/yellow]")

    # ── Phase 2: Extract from persisted PDFs ─────────────────────────────────────
    console.rule("[bold]Phase 2: Extracting from PDFs[/bold]")
    updated = []
    failed = []

    for stock in qualifying:
        ticker = stock["ticker"]
        perf_id = stock["perf_id"]
        result: dict = {"ticker": ticker, "perf_id": perf_id}

        existing_pdfs = sorted(ARTIFACTS_DIR.glob(f"{ticker}_*.pdf"), reverse=True)
        if existing_pdfs:
            pdf_result = _extract_from_pdf(existing_pdfs[0], ticker, perf_id)
            if pdf_result:
                if pdf_result.get("uncertainty"):
                    result["uncertainty"] = pdf_result["uncertainty"]
                if pdf_result.get("ratings_date"):
                    result["ratings_date"] = pdf_result["ratings_date"]

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
            )
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


def _filter_stale_stocks(stocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Filter stocks into qualifying (stale) and skipped (fresh) lists."""
    qualifying = []
    skipped = []

    for stock in stocks:
        if not stock.get("perf_id"):
            continue

        ticker = stock.get("ticker", "").upper()
        has_pdf = bool(list(ARTIFACTS_DIR.glob(f"{ticker}_*.pdf")))
        tier = _assign_tier(stock.get("moat"), stock.get("uncertainty"))
        if not has_pdf or _is_stale(stock.get("ratings_date"), tier):
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


def _click_download_report_button(driver: Chrome) -> None:
    """Click 'Download Report(s)' to open popover, then click 'Download Equity Report'."""
    popover_btn = WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@aria-label, 'Download Report')]")
        )
    )
    popover_btn.click()
    time.sleep(2)

    equity_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[aria-label="Download Equity Report"]')
        )
    )
    equity_btn.click()
    time.sleep(1)


def _click_download_report_button_js(driver: Chrome) -> None:
    """JS-click fallback: scroll into view then fire click via JS (bypasses intercept)."""
    popover_btn = WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.presence_of_element_located(
            (By.XPATH, "//button[contains(@aria-label, 'Download Report')]")
        )
    )
    driver.execute_script("arguments[0].scrollIntoView(true);", popover_btn)
    driver.execute_script("arguments[0].click();", popover_btn)
    time.sleep(2)

    equity_btn = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'button[aria-label="Download Equity Report"]')
        )
    )
    driver.execute_script("arguments[0].click();", equity_btn)
    time.sleep(1)


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

            ratings_date = _parse_pdf_ratings_date(all_text)
            if ratings_date:
                result["ratings_date"] = ratings_date

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


def _parse_pdf_ratings_date(text: str) -> Optional[str]:
    """Extract the most recent analyst activity date from the PDF.

    Primary: Analyst Note date from the Contents table — when an analyst last
    published commentary, even if the FMV dollar value didn't change.
    Fallback: Fair Value as of (quant-only reports with no analyst note).
    Further fallback: Valuation as of (paragraph form used by some stocks).
    """
    # Contents-table section patterns — date in parentheses next to section name.
    # These are the analyst's last-written dates and are the most reliable signal.
    _CONTENTS_SECTIONS = (
        "Analyst Note",
        "Business Strategy & Outlook",
        "Business Strategy and Outlook",
        "Economic Moat",
        "Fair Value and Profit Drivers",
        "Risk and Uncertainty",
        "Capital Allocation",
        "Bulls Say",
    )
    _sec = "|".join(re.escape(s) for s in _CONTENTS_SECTIONS)

    # 1. Contents-table section entries — find ALL matches, return the LATEST.
    #    Main sections share one date; Analyst Notes Archive may have newer entries.
    section_dates = []
    for pat in (
        rf"(?:{_sec})\s*\((\d{{1,2}}\s+[A-Za-z]+\s+\d{{4}})\)",
        rf"(?:{_sec})\s*\(([A-Za-z]+\s+\d{{1,2}},\s+\d{{4}})\)",
    ):
        for m in re.finditer(pat, text, re.IGNORECASE):
            normalized = _normalize_date(m.group(1))
            if normalized:
                section_dates.append(normalized)
    if section_dates:
        return max(section_dates)

    # 2. "Fair Value as of" — find ALL occurrences and take the latest.
    #    Reports can have an old-FMV chart footnote AND the current FMV; latest wins.
    fmv_dates = []
    for pat in (
        r"Fair Value as of\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"Fair Value as of\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    ):
        for m in re.finditer(pat, text, re.IGNORECASE):
            normalized = _normalize_date(m.group(1))
            if normalized:
                fmv_dates.append(normalized)
    if fmv_dates:
        return max(fmv_dates)

    # 3. "Valuation as of" — paragraph form used by some stocks
    for pat in (
        r"Valuation as of\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"Valuation as of\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            normalized = _normalize_date(m.group(1))
            if normalized:
                return normalized

    return None
