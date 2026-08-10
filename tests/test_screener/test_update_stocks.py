"""Tests for screener.service.update_stocks.

`update_stock_data` builds its own `StockAPI` instance internally (it used to
use a module-level `_api` singleton). Tests therefore patch the `StockAPI`
name in the `update_stocks` module namespace with a stub, so no real API
client / Redis connection is ever constructed.
"""

from collections.abc import Callable
from typing import Any

import pytest
import requests

from screener.service import update_stocks
from screener.service.stock_api import MSAPIKeysExhaustedError, CacheOption


UpdateResult = update_stocks.UpdateResult
_SYMBOL_1 = "TEST"
_SYMBOL_2 = "TEST2"


@pytest.fixture(name="fake_api")
def fake_api_fixture(monkeypatch):
    """Return a callable that installs a stub StockAPI backed by `get_info`.

    The stub records every (symbol, cache_option) pair it was called with on
    `.calls` so tests can assert on how the API was driven.
    """
    def install(get_info: Callable[[str, CacheOption], Any]):
        class _FakeStockAPI:
            calls: list[tuple[str, CacheOption]] = []

            def get_info(
                self,
                symbol: str,
                cache_option: CacheOption = CacheOption.CHECK_ALL,
            ):
                type(self).calls.append((symbol, cache_option))
                return get_info(symbol, cache_option)

        monkeypatch.setattr(update_stocks, "StockAPI", _FakeStockAPI)
        return _FakeStockAPI

    return install


def test_update_stocks_successful(fake_api):
    symbols = [_SYMBOL_1, _SYMBOL_2]
    fake_api(lambda sym, cache_option: sym[::-1])

    results = list(update_stocks.update_stock_data(symbols))

    assert {r.symbol for r in results} == set(symbols)
    for r in results:
        assert isinstance(r, UpdateResult)
        assert r.data == r.symbol[::-1]
        assert r.duration >= 0


def test_update_stocks_returns_none_data_on_retry(fake_api):
    symbols = [_SYMBOL_1]

    def fake_get(sym, cache_option):
        raise requests.exceptions.RetryError()
    fake_api(fake_get)

    results = list(update_stocks.update_stock_data(symbols))

    assert len(results) == 1
    res = results[0]
    assert res.symbol == _SYMBOL_1
    assert res.data is None


@pytest.mark.parametrize("exc", [
    requests.exceptions.RetryError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
])
def test_update_stocks_swallows_symbol_level_errors(fake_api, exc):
    def fake_get(sym, cache_option):
        raise exc()
    fake_api(fake_get)

    results = list(update_stocks.update_stock_data([_SYMBOL_1]))

    assert [r.data for r in results] == [None]


def test_update_stocks_raises_on_keys_exhausted(fake_api):
    symbols = [_SYMBOL_1, _SYMBOL_2]

    def fake_get(sym, cache_option):
        if sym == _SYMBOL_2:
            return {"foo": "bar"}
        raise MSAPIKeysExhaustedError()
    fake_api(fake_get)

    with pytest.raises(MSAPIKeysExhaustedError):
        list(update_stocks.update_stock_data(symbols, max_workers=1))


def test_updated_stocks_checks_all_caches(fake_api):
    symbols = [_SYMBOL_1, _SYMBOL_2]
    cached_result = {"foo": "bar"}

    def fake_get(sym, cache_option):
        if cache_option == CacheOption.CHECK_ALL:
            return cached_result
        return None
    stub = fake_api(fake_get)

    results = list(update_stocks.update_stock_data(
        symbols, cache_option=CacheOption.CHECK_ALL))

    assert len(results) == 2
    for r in results:
        assert r.data == cached_result
    assert {c[1] for c in stub.calls} == {CacheOption.CHECK_ALL}


def test_update_stocks_defaults_to_refresh_all(fake_api):
    stub = fake_api(lambda sym, cache_option: sym)

    list(update_stocks.update_stock_data([_SYMBOL_1]))

    assert stub.calls == [(_SYMBOL_1, CacheOption.REFRESH_ALL)]
