"""Tests for scorer.py."""

import pytest
from datetime import date, timedelta

from trader_agent.tools.scorer import (
    buy_score,
    conviction_tier,
    freshness_weight,
    parse_discount,
    parse_stars,
    passes_prefilter,
    ratings_age_days,
    sizing_hint,
)


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


FRESH = _days_ago(30)
STALE_91 = _days_ago(91)
STALE_200 = _days_ago(200)


class TestBuyScore:
    def test_wide_low_fresh(self):
        result = buy_score(0.77, "Wide", "Low", FRESH)
        assert result == pytest.approx(min((1 - 0.77) / 0.5, 1.0) * 1.0 * 1.0 * 1.0)

    def test_narrow_medium_fresh(self):
        result = buy_score(0.85, "Narrow", "Medium", FRESH)
        assert result == pytest.approx(min((1 - 0.85) / 0.5, 1.0) * 0.85 * 0.85 * 1.0)

    def test_none_discount(self):
        assert buy_score(None, "Wide", "Low", FRESH) is None

    def test_unknown_moat(self):
        assert buy_score(0.77, "Unknown", "Low", FRESH) is None

    def test_none_uncertainty(self):
        assert buy_score(0.77, "Wide", None, FRESH) is None


class TestPassesPrefilter:
    def _row(self, stars, discount, moat="Wide", uncertainty="Low"):
        return {"stars": str(stars), "discount": str(discount), "moat": moat, "uncertainty": uncertainty}

    def test_1star_excluded(self):
        passed, reason = passes_prefilter(self._row(1, 0.70))
        assert not passed
        assert reason == "1-star hard exclude"

    def test_2star_high_discount_excluded(self):
        passed, reason = passes_prefilter(self._row(2, 0.80))
        assert not passed
        assert "2-star" in reason

    def test_2star_low_discount_passes(self):
        passed, reason = passes_prefilter(self._row(2, 0.65))
        assert passed
        assert reason is None

    def test_overvalued_excluded(self):
        passed, reason = passes_prefilter(self._row(4, 1.05))
        assert not passed
        assert "overvalued" in reason

    def test_missing_moat_excluded(self):
        row = {"stars": "4", "discount": "0.80", "moat": "", "uncertainty": "Low"}
        passed, reason = passes_prefilter(row)
        assert not passed
        assert "moat" in reason

    def test_unknown_moat_excluded(self):
        row = {"stars": "4", "discount": "0.80", "moat": "Partial", "uncertainty": "Low"}
        passed, reason = passes_prefilter(row)
        assert not passed

    def test_missing_uncertainty_excluded(self):
        row = {"stars": "4", "discount": "0.80", "moat": "Wide", "uncertainty": ""}
        passed, reason = passes_prefilter(row)
        assert not passed
        assert "uncertainty" in reason


class TestConvictionTier:
    def test_strong_buy(self):
        assert conviction_tier(0.70) == "STRONG BUY"

    def test_buy(self):
        assert conviction_tier(0.40) == "BUY"

    def test_watch(self):
        assert conviction_tier(0.20) == "WATCH"

    def test_skip(self):
        assert conviction_tier(0.10) == "SKIP"

    def test_boundary_strong_buy(self):
        assert conviction_tier(0.50) == "STRONG BUY"

    def test_boundary_buy(self):
        assert conviction_tier(0.30) == "BUY"

    def test_boundary_watch(self):
        assert conviction_tier(0.15) == "WATCH"


class TestParseStars:
    def test_unicode_stars(self):
        assert parse_stars("★★★★☆") == 4

    def test_numeric_string(self):
        assert parse_stars("5") == 5

    def test_none(self):
        assert parse_stars(None) is None

    def test_invalid(self):
        assert parse_stars("bad") is None


class TestRatingsAgeDays:
    def test_iso_91_days(self):
        age = ratings_age_days(_days_ago(91))
        assert age == 91

    def test_none(self):
        assert ratings_age_days(None) is None

    def test_mmm_format(self):
        d = date.today() - timedelta(days=10)
        s = d.strftime("%b %d, %Y")
        assert ratings_age_days(s) == 10


class TestFreshnessWeight:
    def test_91_days(self):
        assert freshness_weight(STALE_91) == 0.85

    def test_fresh(self):
        assert freshness_weight(FRESH) == 1.0

    def test_stale(self):
        assert freshness_weight(STALE_200) == 0.70

    def test_none(self):
        assert freshness_weight(None) == 0.70
