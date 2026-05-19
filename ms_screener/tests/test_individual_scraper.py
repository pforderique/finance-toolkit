"""Tests for individual_scraper module."""

import pytest
from datetime import datetime, timedelta

from ms_screener.src.individual_scraper import (
    IndividualScrapeResult,
    _assign_tier,
    _is_stale,
    _filter_stale_stocks,
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
