"""Tests for signals.py."""

import pytest
from dataclasses import dataclass
from datetime import date, timedelta
from unittest.mock import patch

from trader_agent.tools.signals import apply_fmv_flags, detect_fmv_upgrades, flag_stale
from trader_agent.tools.scorer import ScoredStock


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _make_fmv_row(ticker: str, days_ago: int, old_fmv: float, new_fmv: float) -> dict:
    return {
        "ticker": ticker,
        "date": _days_ago(days_ago),
        "old_fmv": str(old_fmv),
        "new_fmv": str(new_fmv),
    }


def _make_stock(ticker: str, ratings_age: int | None = 30, fmv_upgraded: bool = False) -> ScoredStock:
    stale = ratings_age is None or ratings_age > 180
    return ScoredStock(
        ticker=ticker,
        company="Test Co",
        discount=0.75,
        discount_pct=0.25,
        fair_value=100.0,
        last_price=75.0,
        moat="Wide",
        uncertainty="Low",
        stars=4,
        ratings_date=_days_ago(ratings_age) if ratings_age else None,
        ratings_age_days=ratings_age,
        stale_rating=stale,
        conviction="WATCH",
        sizing_hint="monitor, not yet",
        fmv_upgraded=fmv_upgraded,
        price_change_pct=None,
        filter_reason=None,
    )


class TestDetectFmvUpgrades:
    def test_20pct_jump_included(self):
        rows = [_make_fmv_row("AAPL", 30, 100.0, 120.0)]
        result = detect_fmv_upgrades(rows, lookback_days=60, threshold_pct=15.0)
        assert "AAPL" in result
        assert result["AAPL"] == pytest.approx(20.0)

    def test_10pct_jump_excluded(self):
        rows = [_make_fmv_row("MSFT", 20, 100.0, 110.0)]
        result = detect_fmv_upgrades(rows, lookback_days=60, threshold_pct=15.0)
        assert "MSFT" not in result

    def test_outside_window_excluded(self):
        rows = [_make_fmv_row("GOOG", 90, 100.0, 125.0)]
        result = detect_fmv_upgrades(rows, lookback_days=60, threshold_pct=15.0)
        assert "GOOG" not in result

    def test_empty_history(self):
        assert detect_fmv_upgrades([]) == {}

    def test_multiple_entries_uses_oldest_and_newest(self):
        rows = [
            _make_fmv_row("META", 50, 100.0, 110.0),
            _make_fmv_row("META", 10, 110.0, 125.0),
        ]
        result = detect_fmv_upgrades(rows, lookback_days=60, threshold_pct=15.0)
        assert "META" in result
        assert result["META"] == pytest.approx(25.0)


class TestApplyFmvFlags:
    def test_sets_fmv_upgraded_true(self):
        stocks = [_make_stock("AAPL"), _make_stock("MSFT")]
        upgrades = {"AAPL": 20.0}
        result = apply_fmv_flags(stocks, upgrades)
        aapl = next(s for s in result if s.ticker == "AAPL")
        msft = next(s for s in result if s.ticker == "MSFT")
        assert aapl.fmv_upgraded is True
        assert msft.fmv_upgraded is False

    def test_no_upgrades(self):
        stocks = [_make_stock("AAPL")]
        result = apply_fmv_flags(stocks, {})
        assert result[0].fmv_upgraded is False


class TestFlagStale:
    def test_stale_over_180(self):
        stocks = [_make_stock("AAPL", ratings_age=200)]
        result = flag_stale(stocks)
        assert result[0].stale_rating is True

    def test_fresh_not_stale(self):
        stocks = [_make_stock("AAPL", ratings_age=30)]
        result = flag_stale(stocks)
        assert result[0].stale_rating is False

    def test_none_age_is_stale(self):
        stocks = [_make_stock("AAPL", ratings_age=None)]
        result = flag_stale(stocks)
        assert result[0].stale_rating is True
