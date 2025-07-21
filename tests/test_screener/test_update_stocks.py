import pytest
import requests
import redis
import redis.exceptions

from screener.service import update_stocks
from screener.service.stock_api import MSAPIKeysExhaustedError, CacheOption


UpdateResult = update_stocks.UpdateResult
_api = update_stocks._api
_SYMBOL_1 = "TEST"
_SYMBOL_2 = "TEST2"


def test_update_stocks_successful(monkeypatch):
    symbols = [_SYMBOL_1, _SYMBOL_2]
    monkeypatch.setattr(_api, "get_info", lambda sym, cache_option: sym[::-1])

    results = list(update_stocks.update_stock_data(symbols))

    assert {r.symbol for r in results} == set(symbols)
    for r in results:
        assert r.data == r.symbol[::-1]


def test_update_stocks_returns_none_data_on_retry(monkeypatch):
    symbols = [_SYMBOL_1]

    def fake_get(sym, cache_option):
        raise requests.exceptions.RetryError()
    monkeypatch.setattr(_api, "get_info", fake_get)

    results = list(update_stocks.update_stock_data(symbols))

    assert len(results) == 1
    res = results[0]
    assert res.symbol == _SYMBOL_1
    assert res.data is None


def test_update_stocks_raises_on_keys_exhausted(monkeypatch):
    symbols = [_SYMBOL_1, _SYMBOL_2]

    def fake_get(sym, cache_option):
        if sym == _SYMBOL_2:
            return {"foo": "bar"}
        raise MSAPIKeysExhaustedError()
    monkeypatch.setattr(_api, "get_info", fake_get)

    with pytest.raises(MSAPIKeysExhaustedError):
        list(update_stocks.update_stock_data(symbols, max_workers=1))


def test_updated_stocks_checks_all_caches(monkeypatch):
    symbols = [_SYMBOL_1, _SYMBOL_2]
    cached_result = {"foo": "bar"}

    def fake_get(sym, cache_option):
        if cache_option == CacheOption.CHECK_ALL:
            return cached_result
        return None
    monkeypatch.setattr(_api, "get_info", fake_get)

    results = list(update_stocks.update_stock_data(
        symbols, cache_option=CacheOption.CHECK_ALL))

    assert len(results) == 2
    for r in results:
        assert r.data == cached_result
