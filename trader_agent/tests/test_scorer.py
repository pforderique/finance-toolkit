"""Tests for scorer.py."""

import pytest
from datetime import date, timedelta

from trader_agent.tools.scorer import (
    conviction_tier,
    is_stale,
    parse_discount,
    parse_stars,
    passes_prefilter,
    ratings_age_days,
)


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


FRESH = _days_ago(30)
STALE_91 = _days_ago(91)
STALE_200 = _days_ago(200)


class TestConvictionTier:
    def test_5star_fresh_is_strong_buy(self):
        assert conviction_tier(5, stale=False) == "STRONG BUY"

    def test_5star_stale_is_buy(self):
        assert conviction_tier(5, stale=True) == "BUY"

    def test_4star_fresh_is_buy(self):
        assert conviction_tier(4, stale=False) == "BUY"

    def test_4star_stale_is_watch(self):
        assert conviction_tier(4, stale=True) == "WATCH"

    def test_3star_is_watch(self):
        assert conviction_tier(3, stale=False) == "WATCH"

    def test_3star_stale_is_watch(self):
        assert conviction_tier(3, stale=True) == "WATCH"

    def test_2star_is_sell(self):
        assert conviction_tier(2, stale=False) == "SELL"

    def test_1star_is_strong_sell(self):
        assert conviction_tier(1, stale=False) == "STRONG SELL"

    def test_none_star_is_skip(self):
        assert conviction_tier(None, stale=False) == "SKIP"


class TestIsStale:
    def test_fresh_not_stale(self):
        assert is_stale(FRESH) is False

    def test_91d_not_stale(self):
        assert is_stale(STALE_91) is False

    def test_200d_is_stale(self):
        assert is_stale(STALE_200) is True

    def test_none_is_stale(self):
        assert is_stale(None) is True


class TestPassesPrefilter:
    def _row(self, stars, discount):
        return {"stars": str(stars), "discount": str(discount)}

    # 1-2 star names are sell signals — they pass the filter regardless of discount.
    def test_1star_passes_as_sell_signal(self):
        passed, reason = passes_prefilter(self._row(1, 0.70))
        assert passed
        assert reason is None

    def test_2star_passes_as_sell_signal(self):
        passed, reason = passes_prefilter(self._row(2, 0.65))
        assert passed
        assert reason is None

    def test_3star_passes(self):
        passed, reason = passes_prefilter(self._row(3, 0.85))
        assert passed

    def test_overvalued_excluded(self):
        passed, reason = passes_prefilter(self._row(4, 1.05))
        assert not passed
        assert "overvalued" in reason

    def test_4star_passes(self):
        passed, reason = passes_prefilter(self._row(4, 0.80))
        assert passed

    def test_5star_passes(self):
        passed, reason = passes_prefilter(self._row(5, 0.60))
        assert passed


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


class TestParseDiscount:
    def test_ratio(self):
        assert parse_discount("0.89") == pytest.approx(0.89)

    def test_percentage(self):
        assert parse_discount("89%") == pytest.approx(0.89)

    def test_none(self):
        assert parse_discount(None) is None

    def test_invalid(self):
        assert parse_discount("bad") is None
