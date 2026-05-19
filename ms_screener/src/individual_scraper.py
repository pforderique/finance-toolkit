"""Scraper for individual Morningstar stock pages to extract Uncertainty and Ratings_Date."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ms_screener.src.logging_setup import console

BASE_URL = "https://research-morningstar-com.ezproxy.spl.org/quotes/{perf_id}"
TIER_THRESHOLDS = {1: 30, 2: 90, 3: 180}
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
) -> IndividualScrapeResult:
    """
    Scrape individual Morningstar pages for Uncertainty and Ratings_Date.

    Args:
        driver: Already-authenticated Chrome driver (caller owns lifecycle)
        stocks: List of dicts with: ticker, perf_id, ratings_date, uncertainty, moat
        max_stocks: Safety cap on number of stocks to scrape per run
        rate_limit_seconds: Seconds to wait between requests

    Returns:
        IndividualScrapeResult with updated/failed/skipped/pending lists
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

    qualifying, skipped = _filter_stale_stocks(stocks)
    console.print(
        f"[cyan]• Individual scrape: {len(qualifying)} stocks qualify,"
        f" {len(skipped)} skipped (not stale)[/cyan]"
    )

    pending = []
    if len(qualifying) > max_stocks:
        pending = [s["ticker"] for s in qualifying[max_stocks:]]
        qualifying = qualifying[:max_stocks]
        console.print(f"[yellow]• Capped to {max_stocks}; {len(pending)} pending next run[/yellow]")

    updated = []
    failed = []

    for idx, stock in enumerate(qualifying, start=1):
        time.sleep(rate_limit_seconds)
        ticker = stock["ticker"]
        perf_id = stock["perf_id"]
        result = _scrape_with_retry(driver, ticker, perf_id, logger)

        if result:
            updated.append(result)
            console.print(
                f"[green]• Scraped {ticker}:[/green] "
                f"uncertainty={result.get('uncertainty', '—')},"
                f" ratings_date={result.get('ratings_date', '—')}"
            )
        else:
            failed.append(ticker)
            console.print(f"[red]• Failed {ticker}[/red]")

    console.print(f"[cyan]• Skipped {len(skipped)} stocks (not stale)[/cyan]")
    if pending:
        console.print(f"[cyan]• Pending {len(pending)} stocks (hit safety cap, retry next run)[/cyan]")

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
    moat_lower = (moat or "").lower()
    uncertainty_lower = (uncertainty or "").lower()

    if moat_lower == "wide":
        if uncertainty_lower == "low":
            return 3
        elif uncertainty_lower == "medium":
            return 2
        else:
            return 1
    elif moat_lower in {"narrow", "none", ""}:
        return 1
    else:
        return 2 if moat_lower else 2


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

        tier = _assign_tier(stock.get("moat"), stock.get("uncertainty"))
        if _is_stale(stock.get("ratings_date"), tier):
            qualifying.append(stock)
        else:
            skipped.append(stock)

    return qualifying, skipped


def _scrape_with_retry(
    driver: Chrome,
    ticker: str,
    perf_id: str,
    logger: logging.Logger,
    rate_limit: float = 3.0,
) -> Optional[dict]:
    """Scrape one stock page with exponential backoff retry."""
    max_retries = 3
    backoff_times = [6, 12, 24]

    for attempt in range(max_retries + 1):
        try:
            result = _scrape_one(driver, ticker, perf_id)
            return result
        except (TimeoutException, Exception) as exc:
            if attempt >= max_retries:
                logger.error(
                    f"Failed {ticker} ({perf_id}) after {max_retries} retries: {exc}"
                )
                return None

            wait_time = backoff_times[attempt]
            logger.warning(
                f"Failed {ticker} ({perf_id}), attempt {attempt + 1}/{max_retries},"
                f" retrying in {wait_time}s: {exc}"
            )
            time.sleep(wait_time)


def _scrape_one(driver: Chrome, ticker: str, perf_id: str) -> Optional[dict]:
    """Scrape a single stock page for Uncertainty and Ratings_Date."""
    url = BASE_URL.format(perf_id=perf_id)
    driver.get(url)

    WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    page_source = driver.page_source
    soup = BeautifulSoup(page_source, "html.parser")

    result = {"ticker": ticker, "perf_id": perf_id}

    uncertainty = _extract_uncertainty(soup)
    if uncertainty:
        result["uncertainty"] = uncertainty

    ratings_date = _extract_ratings_date(soup)
    if ratings_date:
        result["ratings_date"] = ratings_date

    capital_allocation = _extract_capital_allocation(soup)
    if capital_allocation:
        result["capital_allocation"] = capital_allocation

    return result if result else None


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
    """Extract Ratings_Date from 'Published on' date in analysis header."""
    try:
        for span in soup.find_all("span", class_="date-section"):
            text = span.get_text(strip=True)
            if "published on" in text.lower():
                date_match = span.get_text(strip=True)
                for fmt in ["%b %d, %Y", "%B %d, %Y"]:
                    try:
                        parts = date_match.split("Published on")
                        if len(parts) > 1:
                            date_str = parts[1].strip()
                            normalized = _normalize_date(date_str)
                            if normalized:
                                return normalized
                    except ValueError:
                        continue
        return None
    except Exception as exc:
        console.print(f"[dim]Debug: Ratings_Date parse failed: {exc}[/dim]")
        return None


def _normalize_date(date_str: str) -> Optional[str]:
    """Normalize date string to YYYY-MM-DD format."""
    formats = [
        ("%d %b %Y", None),
        ("%b %d, %Y", None),
        ("%m/%d/%Y", None),
    ]

    for fmt, _ in formats:
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
