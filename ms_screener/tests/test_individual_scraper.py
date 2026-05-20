"""Tests for individual_scraper module."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from ms_screener.src.individual_scraper import (
    IndividualScrapeResult,
    _assign_tier,
    _is_stale,
    _filter_stale_stocks,
    _parse_pdf_uncertainty,
    _parse_pdf_ratings_date,
)


class TestTierAssignment:
    """Test tier assignment logic."""

    def test_wide_low(self):
        """Wide moat + Low uncertainty → Tier 3."""
        assert _assign_tier("Wide", "Low") == 3

    def test_wide_medium(self):
        """Wide moat + Medium uncertainty → Tier 2."""
        assert _assign_tier("Wide", "Medium") == 2

    def test_wide_high(self):
        """Wide moat + High uncertainty → Tier 1."""
        assert _assign_tier("Wide", "High") == 1

    def test_narrow_any(self):
        """Narrow moat + any uncertainty → Tier 1."""
        assert _assign_tier("Narrow", "Low") == 1
        assert _assign_tier("Narrow", "Medium") == 1
        assert _assign_tier("Narrow", "High") == 1

    def test_none_moat_any(self):
        """None moat + any uncertainty → Tier 1."""
        assert _assign_tier("None", "Low") == 1

    def test_unknown_moat(self):
        """Unknown moat or uncertainty → Tier 2 (default)."""
        assert _assign_tier(None, "Medium") == 2
        assert _assign_tier("Wide", None) == 2
        assert _assign_tier(None, None) == 2

    def test_case_insensitive(self):
        """Tier assignment is case-insensitive."""
        assert _assign_tier("wide", "low") == 3
        assert _assign_tier("WIDE", "LOW") == 3


class TestStaleness:
    """Test staleness filtering logic."""

    def test_none_date_always_stale(self):
        """None ratings_date is always stale."""
        assert _is_stale(None, 1) is True
        assert _is_stale(None, 2) is True
        assert _is_stale(None, 3) is True

    def test_empty_string_always_stale(self):
        """Empty string ratings_date is always stale."""
        assert _is_stale("", 1) is True

    def test_fresh_date_not_stale_tier1(self):
        """Fresh date (<30 days) not stale for Tier 1."""
        today = datetime.now().date()
        recent = (today - timedelta(days=20)).strftime("%Y-%m-%d")
        assert _is_stale(recent, 1) is False

    def test_stale_date_at_boundary_tier1(self):
        """At exactly 31 days, considered stale for Tier 1 (threshold 30)."""
        today = datetime.now().date()
        old = (today - timedelta(days=31)).strftime("%Y-%m-%d")
        assert _is_stale(old, 1) is True

    def test_stale_date_tier2_boundary(self):
        """At 91 days, stale for Tier 2 (threshold 90)."""
        today = datetime.now().date()
        old = (today - timedelta(days=91)).strftime("%Y-%m-%d")
        assert _is_stale(old, 2) is True

    def test_stale_date_tier3_boundary(self):
        """At 181 days, stale for Tier 3 (threshold 180)."""
        today = datetime.now().date()
        old = (today - timedelta(days=181)).strftime("%Y-%m-%d")
        assert _is_stale(old, 3) is True

    def test_date_formats(self):
        """Support multiple date formats."""
        today = datetime.now().date()

        iso_date = (today - timedelta(days=31)).strftime("%Y-%m-%d")
        assert _is_stale(iso_date, 1) is True

        us_date = (today - timedelta(days=31)).strftime("%m/%d/%Y")
        assert _is_stale(us_date, 1) is True

        long_date = (today - timedelta(days=31)).strftime("%b %d, %Y")
        assert _is_stale(long_date, 1) is True


class TestFilterStaleStocks:
    """Test stock filtering logic."""

    def test_skip_stocks_with_no_perf_id(self):
        """Stocks without perf_id are skipped."""
        stocks = [
            {"ticker": "AAPL", "perf_id": None, "ratings_date": None, "moat": "Wide", "uncertainty": "Low"},
            {"ticker": "GOOGL", "perf_id": "0P000000AA", "ratings_date": None, "moat": "Wide", "uncertainty": "Low"},
        ]
        qualifying, skipped = _filter_stale_stocks(stocks)
        assert len(qualifying) == 1
        assert qualifying[0]["ticker"] == "GOOGL"

    def test_qualify_stocks_with_no_ratings_date(self):
        """Stocks with no ratings_date are stale (always qualify)."""
        stocks = [
            {"ticker": "AAPL", "perf_id": "0P000000AA", "ratings_date": None, "moat": "Wide", "uncertainty": "Low"},
        ]
        qualifying, skipped = _filter_stale_stocks(stocks)
        assert len(qualifying) == 1
        assert len(skipped) == 0

    def test_fresh_wide_low_skipped_tier3(self):
        """Fresh Wide+Low stock skipped (Tier 3, >180 days threshold)."""
        today = datetime.now().date()
        fresh = (today - timedelta(days=100)).strftime("%Y-%m-%d")
        stocks = [
            {"ticker": "AAPL", "perf_id": "0P000000AA", "ratings_date": fresh, "moat": "Wide", "uncertainty": "Low"},
        ]
        qualifying, skipped = _filter_stale_stocks(stocks)
        assert len(qualifying) == 0
        assert len(skipped) == 1

    def test_stale_wide_low_qualifies_tier3(self):
        """Stale Wide+Low stock qualifies (Tier 3, >180 days threshold)."""
        today = datetime.now().date()
        old = (today - timedelta(days=200)).strftime("%Y-%m-%d")
        stocks = [
            {"ticker": "AAPL", "perf_id": "0P000000AA", "ratings_date": old, "moat": "Wide", "uncertainty": "Low"},
        ]
        qualifying, skipped = _filter_stale_stocks(stocks)
        assert len(qualifying) == 1
        assert len(skipped) == 0


class TestIndividualScrapeResult:
    """Test result dataclass."""

    def test_result_fields(self):
        """IndividualScrapeResult has required fields."""
        result = IndividualScrapeResult(
            updated=[{"ticker": "AAPL", "uncertainty": "Low"}],
            failed=["GOOGL"],
            skipped=["MSFT"],
            pending=["AMZN"],
        )
        assert len(result.updated) == 1
        assert len(result.failed) == 1
        assert len(result.skipped) == 1
        assert len(result.pending) == 1
        assert result.updated[0]["ticker"] == "AAPL"


class TestPdfUncertaintyParsing:
    """Test PDF uncertainty extraction."""

    def test_extract_uncertainty_low(self):
        """Extract 'Low' uncertainty from PDF text."""
        text = "Some header\nUncertainty Level\nLow\nOther content"
        assert _parse_pdf_uncertainty(text) == "Low"

    def test_extract_uncertainty_high(self):
        """Extract 'High' uncertainty from PDF text."""
        text = "Analysis:\nUncertainty: High\nRating: 4"
        assert _parse_pdf_uncertainty(text) == "High"

    def test_extract_uncertainty_very_high(self):
        """Extract 'Very High' uncertainty from PDF text."""
        text = "Uncertainty\nVery High\nDetails..."
        assert _parse_pdf_uncertainty(text) == "Very High"

    def test_no_uncertainty_found(self):
        """Return None if no uncertainty in text."""
        text = "Some content without uncertainty"
        assert _parse_pdf_uncertainty(text) is None

    def test_extract_uncertainty_case_insensitive(self):
        """Uncertainty extraction is case-insensitive."""
        text = "UNCERTAINTY\nmedium\nMore content"
        assert _parse_pdf_uncertainty(text) == "medium"


class TestPdfDateParsing:
    """Test PDF ratings date extraction — Analyst Note date is the primary source."""

    def test_analyst_note_is_primary(self):
        """Analyst Note date from Contents table takes priority over FMV date."""
        text = "Contents\nAnalyst Note (6 May 2026)\nFair Value as of 23 Jul 2024 02:56, UTC"
        assert _parse_pdf_ratings_date(text) == "2026-05-06"

    def test_analyst_note_european_format(self):
        """Analyst Note with day-first format."""
        text = "Analyst Note (4 May 2026)\nBusiness Description"
        assert _parse_pdf_ratings_date(text) == "2026-05-04"

    def test_fair_value_fallback(self):
        """Fall back to Fair Value as of when no Analyst Note present (quant reports)."""
        text = "Fair Value as of 30 Apr 2026 10:09, UTC"
        assert _parse_pdf_ratings_date(text) == "2026-04-30"

    def test_ignores_report_generation_date(self):
        """Report generation date in header must NOT be returned."""
        text = "Report as of 19 May 2026 05:46, UTC\nAnalyst Note (19 Feb 2026)"
        assert _parse_pdf_ratings_date(text) == "2026-02-19"

    def test_valuation_as_of_fallback(self):
        """Valuation as of paragraph form used by some stocks."""
        text = "Valuation as of 18 May 2026\nMore analysis..."
        assert _parse_pdf_ratings_date(text) == "2026-05-18"

    def test_no_date_found(self):
        """Return None if no recognisable date pattern in text."""
        text = "Some content without any date"
        assert _parse_pdf_ratings_date(text) is None

    def test_business_strategy_section_beats_old_fmv_footnote(self):
        """Business Strategy & Outlook Contents entry wins over old FMV chart footnote."""
        text = (
            "Fair Value as of 3 Feb 2026 20:13, UTC.\n"
            "Business Strategy & Outlook (15 May 2026)\n"
        )
        assert _parse_pdf_ratings_date(text) == "2026-05-15"

    def test_fair_value_latest_wins_over_chart_footnote(self):
        """When only Fair Value as of present, latest date wins over old chart footnote."""
        text = (
            "Fair Value as of 3 Feb 2026 20:13, UTC.\n"
            "Fair Value as of 16 May 2026 02:05, UTC\n"
        )
        assert _parse_pdf_ratings_date(text) == "2026-05-16"
