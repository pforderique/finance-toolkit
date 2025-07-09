import http
import time
from collections import deque

import pytest
import requests
import pydantic
from unittest.mock import MagicMock

from screener.core.api_client import APIClient
from screener.core.cache import FakeCache
from screener.core.rate_limiter import RateLimiter


_BASE_URL = pydantic.HttpUrl("https://api.example.com")
_ROUTE = "test/route"
_JSON_DATA = {"key": "value"}


def make_response(status_code=200, json_data=None, headers=None):
    """Helper to build a fake requests.Response."""
    response = requests.Response()
    response.status_code = status_code
    response._content = b""
    if headers:
        response.headers = headers
    response.json = lambda *args, **kwargs: json_data or {}
    return response


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Disable real sleeps in all tests."""
    monkeypatch.setattr(time, "sleep", lambda _: None)


@pytest.fixture
def client():
    """An APIClient wired up with a fake cache and real rate limiter."""
    return APIClient(
        base_url=_BASE_URL,
        api_cache=FakeCache[str, dict](),
        api_rate_limiter=RateLimiter(max_calls=1, period=0.1),
        timeout=0.1,
    )


@pytest.fixture
def response_queue(monkeypatch, client):
    """A queue to control responses from the API client."""
    queue = deque()

    def fake_get(url, params=None, timeout=None):
        try:
            return queue.popleft()
        except IndexError:
            pytest.fail("No more responses in queue!")

    monkeypatch.setattr(client.session, "get", fake_get)
    return queue


class TestAPIClient:
    def test_get_returns_data(self, client, response_queue):
        response_queue.append(make_response(json_data=_JSON_DATA))
        assert client.get(_ROUTE) == _JSON_DATA

    def test_retry_then_success(self, client, response_queue):
        response_queue.append(make_response(http.HTTPStatus.TOO_MANY_REQUESTS))
        response_queue.append(make_response(http.HTTPStatus.OK, json_data=_JSON_DATA))

        result = client.get(_ROUTE, params={}, check_cache=False, retry=2)

        assert result == _JSON_DATA

    def test_non_retriable_error_raises(self, client, response_queue):
        response_queue.append(make_response(http.HTTPStatus.NOT_FOUND))

        with pytest.raises(requests.RequestException):
            client.get(_ROUTE, check_cache=False, retry=2)

    def test_cache_hit_prevents_second_http(self, client, response_queue, monkeypatch):
        original = client.session.get
        spy_get = MagicMock(side_effect=original)
        monkeypatch.setattr(client.session, "get", spy_get)
        response_queue.append(make_response(json_data=_JSON_DATA))
        params = {"a": "b"}

        first = client.get(_ROUTE, params=params, check_cache=True, retry=1)
        second = client.get(_ROUTE, params=params, check_cache=True, retry=1)

        assert first == _JSON_DATA
        assert second == _JSON_DATA
        assert spy_get.call_count == 1

    def test_rate_limit_sleep_called(self, client, response_queue, monkeypatch):
        response_queue.append(make_response(json_data=_JSON_DATA))
        response_queue.append(make_response(json_data=_JSON_DATA))

        client.api_rate_limiter = RateLimiter(max_calls=1, period=1e-10)

        sleeper = MagicMock()
        monkeypatch.setattr(time, "sleep", sleeper)

        client.get(_ROUTE, check_cache=False, retry=1)
        client.get(_ROUTE, check_cache=False, retry=1)

        # Only one sleep call with non-zero wait duration for the rate limit
        assert sleeper.call_count == 1
        waited = sleeper.call_args[0][0]
        assert waited > 0
