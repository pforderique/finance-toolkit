"""HTTP API client with built-in caching and rate limiting."""

from collections.abc import Callable, Mapping
import http
import logging
import time
from typing import Any
import urllib.parse

import requests
import requests.exceptions
import validators

from screener.core import cache
from screener.core import rate_limiter


_logger = logging.getLogger(__name__)


def _trunc(text: str, max_length: int = 100) -> str:
    """Truncate text to a maximum length, appending '...' if truncated."""
    return f"{text[:max_length]}..." if len(text) > max_length else text


class APIClient:
    """
    Generic HTTP API client with caching and rate limiting capabilities.

    Attributes:
        base_url: Base URL for the API (e.g. "https://api.example.com").
        cache: Cache instance for storing GET responses.
        rate_limiter: RateLimiter instance used to control request rate.
        timeout: Default timeout for HTTP requests in seconds.
        session: The requests session used for making HTTP calls.
    """

    def __init__(
        self,
        base_url: str,
        api_cache: cache.Cache | None = None,
        api_rate_limiter: rate_limiter.RateLimiter | None = None,
        timeout: float = 5.0,
    ):
        """
        Initialize the API client.

        Args:
            base_url: Base URL for the API (e.g. "https://api.example.com").
            cache: Cache instance for storing GET responses.
            rate_limiter: RateLimiter instance used to control request rate.
            timeout: Default timeout for HTTP requests in seconds.
        """
        if not validators.url(base_url):
            raise ValueError(f"Invalid base URL: {base_url}")

        self.base_url = base_url
        self.cache = api_cache
        self.rate_limiter = api_rate_limiter
        self.timeout = timeout
        self.session = requests.Session()

    def get(
        self,
        route: str,
        params: Mapping[str, Any] | None = None,
        check_cache: bool = True,
        retry: int = 1,
    ) -> Any:
        """
        Perform a GET request to `{base_url}/{route}` with query parameters.

        If `check_cache` is True, attempt to return cached response first.
        On cache miss, or if check_cache=False, will rate-limit, perform the
        HTTP request, cache the JSON response, and return it.

        Args:
            route: API route (e.g. "v1/data").
            params: Dictionary of query parameters.
            check_cache: If True, use cache.
            retry: Number of retries on request failure.

        Returns:
            Parsed JSON response, or raises an exception on repeated failures.
        """
        params = params or {}
        route_key = self._make_route_key(route, params)

        if check_cache:
            if self.cache is None:
                raise ValueError(
                    f"{__class__.__name__} instance does not have a cache."
                )

            if (cached_value := self.cache.get(route_key)) is not None:
                return cached_value

        url = urllib.parse.urljoin(str(self.base_url), route)

        data = self._call_with_backoff(
            lambda: self.session.get(url, params=params, timeout=self.timeout),
            retry,
            route_key
        )

        if self.cache is not None:
            self.cache.set(route_key, data)

        return data

    def _make_route_key(self, route: str, params: Mapping[str, Any]) -> str:
        """Create a unique route key from route and params."""
        parts = [f"{k}={v}" for k, v in sorted(params.items())]
        query = "&".join(parts)
        key = f"{route}?{query}" if query else route
        return key

    def _call_with_backoff(
        self,
        func: Callable[[], requests.Response],
        retry: int,
        route_key: str
    ) -> Any:
        retry_wait = 0.0
        backoff = 0.5
        backoff_factor = 1.5
        max_retry_wait_time = 10.0

        for attempt in range(1, retry + 1):
            if attempt > 1 and retry_wait > 0:
                _logger.warning(
                    "[%s] retry %d/%d - waiting %.2fs",
                    route_key,
                    attempt,
                    retry,
                    retry_wait
                )
                time.sleep(retry_wait)
            elif attempt == 1:
                _logger.debug("[%s] fetching...", route_key)

            if self.rate_limiter is not None:
                if (wait := self.rate_limiter.wait()) > 0:
                    _logger.warning(
                        "[%s] waiting %.2fs to avoid hitting rate limit",
                        route_key,
                        wait,
                    )
                    time.sleep(wait)

            response = func()

            _logger.debug(
                "[%s] HTTP %d %s - text %s",
                route_key,
                response.status_code,
                response.reason,
                _trunc(response.text)
            )

            if response.status_code == http.HTTPStatus.OK:
                data = response.json()
                if self.cache is not None:
                    self.cache.set(route_key, data)

                    return data

            if not self._is_retriable_error(response):
                _logger.error(
                    "[%s] HTTP %d %s - not a retriable error, raising HTTPError"
                    " - %s",
                    route_key,
                    response.status_code,
                    response.reason,
                    _trunc(response.text)
                )
                raise requests.exceptions.HTTPError(
                    f"[{route_key}] non-retriable error HTTP"
                    f" {response.status_code} {response.reason}"
                    f" - {_trunc(response.text)}"
                )

            _logger.warning("[%s] Received retriable error: %s",
                            route_key, _trunc(response.text))
            retry_wait = min(retry_wait + backoff, max_retry_wait_time)
            backoff *= backoff_factor

        err = requests.exceptions.RetryError(
            f"[{route_key}] HTTP {response.status_code} after {attempt}"
            f" {'tries' if attempt > 1 else 'try'}. Last response: "
            f"{response.text}"
        )
        _logger.error("[%s] Failed to call API after %d attempts. Last response: %s",
                      route_key, attempt, _trunc(response.text), exc_info=False)
        raise err

    def _is_retriable_error(self, response: requests.Response) -> bool:
        """Check if the response indicates a retryable error."""
        return response.status_code in (
            http.HTTPStatus.TOO_MANY_REQUESTS,
            http.HTTPStatus.INTERNAL_SERVER_ERROR,
            http.HTTPStatus.BAD_GATEWAY,
            http.HTTPStatus.SERVICE_UNAVAILABLE,
        )
