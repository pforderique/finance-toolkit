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

# Helper to build fake APIClient.get responses
def fake_client_get_factory(responses: list[Any]):
    """
    Return a stub function for APIClient.get that pops
    the next response based on the route key.
    """
    queue = deque(responses)


    def stub(route: str, params=None, check_cache=True, retry=None):
        if not queue:
            pytest.fail(f"No more stubbed responses for route {route!r}")
        return queue.popleft()

    # allow test to enqueue
    stub.queue = queue
    return stub


class TestStockAPI:
    def test_get_info_happy_path(self):
        api = StockAPI(redis_url=None)
        stub = fake_client_get_factory([
            # 1) auto-complete
            [{
                "performanceId": "P1",
                "Name": "Test Corp",
                "RegionAndTicker": "US:TEST"
            }],
            # 2) fair value raw
            {
                "chart": {"chartDatums": {"recent": {
                    "latestFairValue": "100",
                    "uncertainty": "5",
                    "fairValueDate": "2025-07-11"
                }}}
            },
            # 3) latest price raw
            {
                "lastPrice": "90",
                "dayChange": "-2",
                "dayChangePer": "-2%"
            },
            # 4) star rating raw
            {"starRating": "4"}
        ])
        api._client.get = stub

        result = api.get_info("TEST")

        assert result is not None
        assert result["name"] == "Test Corp"
        assert result["performanceId"] == "P1"
        assert result["ticker"] == "TEST"
        assert result["latestFairValue"] == "100"
        assert result["uncertainty"] == "5"
        assert result["fairValueDate"] == "2025-07-11"
        assert result["lastPrice"] == "90"
        assert result["dayChange"] == "-2"
        assert result["dayChangePer"] == "-2%"
        assert result["starRating"] == "4"
        assert pytest.approx(result["discount"], rel=1e-6) == 0.9
        datetime.datetime.fromisoformat(result["lastCachedDate"])

        # Ensure 2nd call returns cached without further client.get calls
        stub.queue.clear()
        result2 = api.get_info("TEST")
        assert result2 is result

    @pytest.mark.parametrize("missing_step", [
        "ticker", "fmv", "price", "rating"
    ])
    def test_get_info_missing_data_returns_none(self, missing_step):
        api = StockAPI(redis_url=None)
        stub = fake_client_get_factory([])

        # Stub sequentially: insert None or incomplete at the missing step
        # 1) ticker info
        if missing_step == "ticker":
            stub.queue.append([])
        else:
            stub.queue.append([{
                "performanceId": "P2",
                "Name": "Name2",
                "RegionAndTicker": "US:XYZ"
            }])

        # 2) fmv
        if missing_step == "fmv":
            stub.queue.append({})
        else:
            stub.queue.append({"chart": {"chartDatums": {"recent": {
                "latestFairValue": "50",
                "uncertainty": "2",
                "fairValueDate": "2025-07-11"
            }}}})

        # 3) price
        if missing_step == "price":
            stub.queue.append({})
        else:
            stub.queue.append({
                "lastPrice": "48",
                "dayChange": "0",
                "dayChangePer": "0%"
            })

        # 4) rating
        if missing_step == "rating":
            stub.queue.append({})
        else:
            stub.queue.append({"starRating": "3"})

        api._client.get = stub

        assert api.get_info("XYZ") is None

    def test_get_info_with_non_numeric_discount(self):
        api = StockAPI(redis_url=None)
        stub = fake_client_get_factory([
            # 1) auto-complete
            [{
                "performanceId": "P1",
                "Name": "Test Corp",
                "RegionAndTicker": "US:TEST"
            }],
            # 2) fair value raw
            {
                "chart": {"chartDatums": {"recent": {
                    "latestFairValue": "N/A",
                    "uncertainty": "5",
                    "fairValueDate": "2025-07-11"
                }}}
            },
            # 3) latest price raw
            {
                "lastPrice": "N/A",
                "dayChange": "-2",
                "dayChangePer": "-2%"
            },
            # 4) star rating raw
            {"starRating": "3"}
        ])
        api._client.get = stub

        result = api.get_info("TEST")

        assert result is not None
        assert result["discount"] is None

    def test_singleton_behavior(self):
        a1 = StockAPI(redis_url=None)
        a2 = StockAPI(redis_url=None)
        assert a1 is a2
