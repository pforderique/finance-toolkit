"""StockAPI singleton for querying stock data using Morningstar."""

from collections.abc import Container, Iterable
from concurrent.futures import ThreadPoolExecutor
import datetime
import threading
import itertools
import logging
from typing import Any

import pydantic
import redis

from screener import config
from screener.core import api_client
from screener.core import cache
from screener.core import rate_limiter


APIClient = api_client.APIClient
RateLimiter = rate_limiter.RateLimiter
RedisCache = cache.RedisCache
RedisUrl = cache.RedisUrl

logger = logging.getLogger(__name__)

_UNEXPECTED_RESPONSE_MSG = "[%s] unexpected response format in %s - %s"


def _has_keys(container: Container, keys: Iterable[str]) -> bool:
    """Return True if all keys are present in the container."""
    return all(key in container for key in keys)


class StockInfo(pydantic.BaseModel):
    """Model for stock information."""

    name: str
    ticker: str
    performanceId: str
    lastPrice: float
    dayChange: float
    dayChangePer: float
    latestFairValue: float | None = None
    discount: float | None = None
    uncertainty: str | None = None
    starRating: int | None = None
    fairValueDate: str | None = None
    lastCachedDate: str | None = None


class StockAPI:
    """
    Singleton for querying stock data from Morningstar.

    Features:
      • cache for low level API responses
      • cache for stock info
      • RateLimiter: enforces API call rate limits
      • Automatic API key rotation on HTTP errors
    """

    _instance: "StockAPI | None" = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        redis_url: RedisUrl | None = None,
    ):
        # Prevent double‐init on singleton
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self._api_keys = config.MORNINGSTAR_API_KEYS.copy()
        self._key_cycle = itertools.cycle(self._api_keys)

        self._rate_limiter = RateLimiter(
            max_calls=config.RATE_LIMIT_PER_SECOND,
            period=1.0,
        )

        redis_url = RedisUrl.parse_url(config.REDIS_URL)
        self._redis_client = redis.Redis.from_url(redis_url.get_full_url())
        self._ms_cache = RedisCache.from_client(
            self._redis_client, namespace="msapi")
        self.stock_cache = RedisCache.from_client(
            self._redis_client, namespace="stockapi")

        self._client = APIClient(
            base_url=config.MORNINGSTAR_API_BASE_URL,
            api_cache=self._ms_cache,
            api_rate_limiter=self._rate_limiter,
            timeout=config.MORNINGSTAR_API_TIMEOUT,
        )
        self._client.session.headers.update({
            "x-rapidapi-host": config.MORNINGSTAR_API_BASE_URL.split("//")[-1],
        })
        self._use_api_key(next(self._key_cycle))

        self._default_max_retries = config.MORNINGSTAR_API_MAX_RETRIES

    def _use_api_key(self, key: str) -> None:
        self._client.session.headers.update({
            "x-rapidapi-key": key,
        })

    def get_info(
        self,
        symbol: str,
        check_cache: bool = True
    ) -> StockInfo | None:
        """
        Fetch aggregated stock info for `symbol`.

        Args:
            symbol: Stock symbol to query (e.g. "AAPL").
            check_cache: If True, will first check the stock_cache for existing data.

        Returns:
            Parsed JSON response with stock info or None if not found.
        """
        if check_cache and (data := self.stock_cache.get(symbol)) is not None:
            return data

        # Fetch ticker info to get performance ID
        if (ticker_info := self._fetch_ticker_info(symbol)) is None:
            return None

        performance_id = ticker_info["PerformanceId"]

        ms_results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                "fmv": executor.submit(
                    self._fetch_fmv_info, performance_id, check_cache
                ),
                "price": executor.submit(
                    self._fetch_latest_price_info, performance_id, check_cache
                ),
                "star_rating": executor.submit(
                    self._fetch_star_rating_info, performance_id, check_cache
                ),
            }

            for name, future in futures.items():
                if (result := future.result()) is None:
                    return None

                ms_results[name] = result

        fmv_info = ms_results["fmv"]
        price_info = ms_results["price"]
        star_rating_info = ms_results["star_rating"]

        # Calculate additional fields
        last_price = float(price_info["lastPrice"])
        if (fmv := fmv_info.get("latestFairValue")) is None:
            logger.warning(
                "[%s] non-numeric values - fmv_info %s", symbol, fmv_info,
            )
            latest_fair_value = None
            discount = None
        else:
            latest_fair_value = float(fmv)
            discount = last_price / latest_fair_value

        try:
            star_rating = int(star_rating_info["starRating"])
        except ValueError:
            # handle "N/A" cases
            logger.warning(
                "[%s] non-numeric values - star_rating_info %s", symbol, star_rating_info,
            )
            star_rating = None

        stock_info = {
            "name": ticker_info["Name"],
            "performanceId": performance_id,
            "ticker": ticker_info["RegionAndTicker"].split(":")[-1],
            "latestFairValue": latest_fair_value,
            "uncertainty": fmv_info["uncertainty"],
            "fairValueDate": fmv_info["fairValueDate"],
            "lastPrice": last_price,
            "dayChange": float(price_info["dayChange"]),
            "dayChangePer": float(price_info["dayChangePer"]),
            "starRating": star_rating,
            "discount": discount,
            "lastCachedDate": datetime.datetime.now().isoformat(),
        }

        self.stock_cache.set(symbol, stock_info)

        return StockInfo.model_validate(stock_info)

    def _fetch_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        """Fetch ticker info for a given symbol.

        Args:
            symbol: Stock symbol to query (e.g. "AAPL").

        Returns:
            Parsed JSON response with ticker info or None if not found.

        Returned dict contains:
            - PerformanceId: Unique identifier for the stock.
            - Name: Full name of the stock.
            - RegionAndTicker: Region and ticker in the format "Region:Ticker".
        """
        auto_complete_route = "market/v3/auto-complete"
        ticker_infos = self._client.get(
            auto_complete_route,
            params={"q": symbol},
            check_cache=True,  # Always check cache for performance ID
            retry=self._default_max_retries
        )

        if not ticker_infos:
            logger.info("[%s] ticker not found in %s.",
                        symbol, auto_complete_route)
            return None

        ticker_info = ticker_infos[0]
        if not _has_keys(
            ticker_info,
            ("PerformanceId", "Name", "RegionAndTicker")
        ):
            logger.error(_UNEXPECTED_RESPONSE_MSG,
                         symbol, auto_complete_route, ticker_info)
            return None

        logger.debug("[%s] fetched ticker info from %s: %s",
                     symbol, auto_complete_route, ticker_info)
        return ticker_info

    def _fetch_fmv_info(
        self,
        performance_id: str,
        check_cache: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch Fair Market Value (FMV) data for a given performance ID.

        Args:
            performance_id: Unique identifier for the stock.
            check_cache: If True, will first check the stock_cache for existing data.

        Returns:
            Parsed JSON response with FMV data or None if not found.

        Returned dict contains:
            - latestFairValue: The latest fair value of the stock.
            - uncertainty: The uncertainty range of the fair value.
            - fairValueDate: The date of the fair value calculation.
        """
        fmv_route = "stock/v2/get-price-fair-value"
        fair_value_data_raw = self._client.get(
            fmv_route,
            params={"performanceId": performance_id},
            check_cache=check_cache,
            retry=self._default_max_retries
        )

        if not fair_value_data_raw:
            logger.info("[%s] fair value data not found in %s.",
                        performance_id, fmv_route)
            return None

        try:
            fmv_info = (
                fair_value_data_raw["chart"]["chartDatums"]["recent"]
            )
        except KeyError as e:
            logger.error(
                _UNEXPECTED_RESPONSE_MSG,
                performance_id, fmv_route, fair_value_data_raw, exc_info=e
            )
            return None

        if not _has_keys(
            fmv_info,
            ("latestFairValue", "uncertainty", "fairValueDate")
        ):
            logger.error(
                _UNEXPECTED_RESPONSE_MSG,
                performance_id, fmv_route, fmv_info
            )
            return None

        logger.debug(
            "[%s] fetched FMV info from %s: %s", performance_id, fmv_route, fmv_info
        )
        return fmv_info

    def _fetch_latest_price_info(
        self,
        performance_id: str,
        check_cache: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch latest price data for a given performance ID.

        Args:
            performance_id: Unique identifier for the stock.
            check_cache: If True, will first check the cache for existing data.

        Returns:
            Parsed JSON response with latest price data or None if not found.

        Returned dict contains:
            - lastPrice: The latest price of the stock.
            - dayChange: The change in price for the day.
            - dayChangePer: The percentage change in price for the day.
        """
        price_route = "stock/v2/get-mini-chart-realtime-data"
        price_data = self._client.get(
            price_route,
            params={"performanceId": performance_id},
            check_cache=check_cache,
            retry=self._default_max_retries
        )

        if not price_data:
            logger.info("[%s] price data not found in %s.",
                        performance_id, price_route)
            return None

        if not _has_keys(price_data, ("lastPrice", "dayChange", "dayChangePer")):
            logger.error(_UNEXPECTED_RESPONSE_MSG,
                         performance_id, price_route, price_data)
            return None

        logger.debug(
            "[%s] fetched price data from %s: %s",
            performance_id, price_route, price_data
        )
        return price_data

    def _fetch_star_rating_info(
        self,
        performance_id: str,
        check_cache: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch star rating data for a given performance ID.

        Args:
            performance_id: Unique identifier for the stock.
            check_cache: If True, will first check the cache for existing data.

        Returns:
            Parsed JSON response with star rating data or None if not found.

        Returned dict contains:
            - starRating: The star rating of the stock.
        """
        ratings_route = "stock/v2/get-security-info"
        ratings_data = self._client.get(
            ratings_route,
            params={"performanceId": performance_id},
            check_cache=check_cache,
            retry=self._default_max_retries
        )

        if not ratings_data:
            logger.info("[%s] ratings data not found in %s.",
                        performance_id, ratings_route)
            return None

        if not _has_keys(ratings_data, ("starRating",)):
            logger.error(_UNEXPECTED_RESPONSE_MSG,
                         performance_id, ratings_route, ratings_data)
            return None

        logger.debug(
            "[%s] fetched star rating from %s: %s",
            performance_id, ratings_route, ratings_data
        )
        return ratings_data

    def __del__(self):
        """Clean up resources on deletion."""
        if hasattr(self, "_redis_client") and self._redis_client is not None:
            self._redis_client.close()
