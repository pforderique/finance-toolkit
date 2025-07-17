import datetime
import pytest
from collections import deque
from typing import Any, Dict

import pydantic
import redis

from screener.service.stock_api import StockAPI
from screener.core.cache import RedisCache, RedisUrl, FakeCache
from screener.core.rate_limiter import RateLimiter
from screener import config


# Ensure singleton is reset between tests
@pytest.fixture(autouse=True)
def reset_singleton():
    StockAPI._instance = None
    yield
    StockAPI._instance = None

# Patch out redis client and RedisCache.from_url to use an in-memory FakeCache
@pytest.fixture(autouse=True)
def patch_redis_caches(monkeypatch):
    monkeypatch.setattr(
        redis.Redis, "from_url", classmethod(lambda *args, **kwargs: None)
    )
    monkeypatch.setattr(
        RedisCache,
        "from_client",
        classmethod(lambda *args, **kwargs: FakeCache[str, Any]())
    )

# Ensure config values are predictable
@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    monkeypatch.setattr(config, "MORNINGSTAR_API_KEYS", ["key1", "key2"])
    monkeypatch.setattr(config, "RATE_LIMIT_PER_SECOND", 1)
    monkeypatch.setattr(config, "MORNINGSTAR_API_BASE_URL", "https://api.test")
    monkeypatch.setattr(config, "MORNINGSTAR_API_TIMEOUT", 0.1)
    monkeypatch.setattr(config, "MORNINGSTAR_API_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:6379/0")
def fake_client_get_factory(responses: list[Any]):
    """
    Given a list of 4 responses [auto_complete, fmv, price, rating],
    returns a stub that returns the correct one by `route`.
    """
    ROUTES = [
        "market/v3/auto-complete",
        "stock/v2/get-price-fair-value",
        "stock/v2/get-mini-chart-realtime-data",
        "stock/v2/get-security-info",
    ]
    mapping = dict(zip(ROUTES, responses))

    def stub(route: str, params=None, check_cache=True, retry=None):
        if route not in mapping:
            pytest.fail(f"No stubbed response for route {route!r}")
        return mapping[route]

    return stub

class TestStockAPI:
    def test_get_info_happy_path(self):
        api = StockAPI()
        stub = fake_client_get_factory([
            # auto-complete
            [{
                "PerformanceId": "P1",
                "Name": "Test Corp",
                "RegionAndTicker": "US:TEST"
            }],
            # fmv
            {
                "chart": {"chartDatums": {"recent": {
                    "latestFairValue": "100",
                    "uncertainty": "HIGH",
                    "fairValueDate": "2025-07-11"
                }}}
            },
            # price
            {
                "lastPrice": 90,
                "dayChange": -2,
                "dayChangePer": -0.02
            },
            # rating
            {"starRating": "4"}
        ])
        api._client.get = stub

        result = api.get_info("TEST")

        assert result is not None
        assert result.name == "Test Corp"
        assert result.performanceId == "P1"
        assert result.ticker == "TEST"
        assert result.latestFairValue == 100
        assert result.uncertainty == "HIGH"
        assert result.fairValueDate == "2025-07-11"
        assert result.lastPrice == 90
        assert result.dayChange == -2
        assert result.dayChangePer == -0.02
        assert result.starRating == 4
        assert pytest.approx(result.discount, rel=1e-6) == 0.9
        datetime.datetime.fromisoformat(str(result.lastCachedDate))

        assert api.stock_cache.has("TEST")

        result2 = api.get_info("TEST", check_cache=True)
        assert result2 is not None
        assert result2.model_dump() == result.model_dump()

    @pytest.mark.parametrize("missing_step", [
        "ticker", "fmv", "price", "rating"
    ])
    def test_get_info_missing_data_returns_none(self, missing_step):
        api = StockAPI()

        # Build a list of four placeholders, then overwrite only the missing step
        placeholders: list[Any] = [None, None, None, None]

        # 1) ticker
        if missing_step == "ticker":
            placeholders[0] = []
        else:
            placeholders[0] = [{
                "PerformanceId": "P2",
                "Name": "Name2",
                "RegionAndTicker": "US:XYZ"
            }]

        # 2) fmv
        if missing_step == "fmv":
            placeholders[1] = {}
        else:
            placeholders[1] = {"chart": {"chartDatums": {"recent": {
                "latestFairValue": "50",
                "uncertainty": "HIGH",
                "fairValueDate": "2025-07-11"
            }}}}

        # 3) price
        if missing_step == "price":
            placeholders[2] = {}
        else:
            placeholders[2] = {
                "lastPrice": 48,
                "dayChange": -2,
                "dayChangePer": -0.04
            }

        # 4) rating
        if missing_step == "rating":
            placeholders[3] = {}
        else:
            placeholders[3] = {"starRating": "3"}

        api._client.get = fake_client_get_factory(placeholders)
        assert api.get_info("XYZ") is None

    def test_get_info_with_non_numeric_discount(self):
        api = StockAPI()
        stub = fake_client_get_factory([
            # auto-complete
            [{
                "PerformanceId": "P1",
                "Name": "Test Corp",
                "RegionAndTicker": "US:TEST"
            }],
            # fmv (non-numeric)
            {
                "chart": {"chartDatums": {"recent": {
                    "latestFairValue": None,
                    "uncertainty": "HIGH",
                    "fairValueDate": "2025-07-11"
                }}}
            },
            {
                "lastPrice": 23,
                "dayChange": -2,
                "dayChangePer": -0.02
            },
            # rating
            {"starRating": "3"}
        ])
        api._client.get = stub

        result = api.get_info("TEST")
        assert result is not None
        assert result.discount is None

    def test_singleton_behavior(self):
        a1 = StockAPI()
        a2 = StockAPI()
        assert a1 is a2