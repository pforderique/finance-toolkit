from typing import Any

import redis
import pydantic
import pytest

from screener.core import cache


RedisCache = cache.RedisCache
RedisUrl = cache.RedisUrl

_KEY = "test_key"
_VALUE = "test_value"


class FakeRedis:

    def __init__(self):
        self.store = {}
        self.closed = False

    def get(self, key) -> Any | None:
        return self.store.get(key)

    def set(self, key, value) -> bool:
        self.store[key] = value
        return True

    def exists(self, key) -> int:
        return int(key in self.store)

    def close(self):
        self.closed = True


class TestRedisUrl:

    def test_valid_model(self):
        r = RedisUrl(base_url="example.com", port=6380, db=2)
        assert r.get_full_url() == "redis://example.com:6380/2"

    def test_str_method(self):
        r = RedisUrl(base_url="example.com", port=6380, db=2)
        assert str(r) == "redis://example.com:6380/2"

    @pytest.mark.parametrize("base_url, port, db", [
        ("example.com", 6379, -1),  # Invalid db
        ("", 6379, 0),              # Empty base_url
    ])
    def test_invalid_model_raises(self, base_url, port, db):
        with pytest.raises(pydantic.ValidationError):
            RedisUrl(base_url=base_url, port=port, db=db)


class TestRedisCache:

    @pytest.fixture(autouse=True)
    def patch_redis(self, monkeypatch):
        fake_redis = FakeRedis()
        monkeypatch.setattr(
            redis.Redis,
            "from_url",
            lambda *args, **kwargs: fake_redis
        )
        return fake_redis

    def test_set_and_get(self, patch_redis):
        cache_instance = RedisCache(RedisUrl(base_url="unused"))

        cache_instance.set(_KEY, _VALUE)

        assert cache_instance.get(_KEY) == _VALUE

    def test_get_with_different_namespaces(self, patch_redis):
        url = RedisUrl(base_url="unused")
        cache1 = RedisCache(url, namespace="ns1")
        cache2 = RedisCache(url, namespace="ns2")
        cache1_value = "val"
        cache2_value = "a_different_val"
        cache1.set(_KEY, cache1_value)
        cache2.set(_KEY, cache2_value)

        assert cache1.get(_KEY) == cache1_value
        assert cache2.get(_KEY) == cache2_value

    def test_has_key(self, patch_redis):
        cache_instance = RedisCache(RedisUrl(base_url="unused"))

        assert not cache_instance.has(_KEY)
        cache_instance.set(_KEY, _VALUE)
        assert cache_instance.has(_KEY)

    def test_get_non_existent_key(self, patch_redis):
        cache_instance = RedisCache(RedisUrl(base_url="unused"))
        assert cache_instance.get(_KEY) is None

    def test_set_and_get_pickleable_object(self, patch_redis):
        cache_instance = RedisCache(RedisUrl(base_url="unused"))
        value = {"key": "value", "number": 42}

        cache_instance.set(_KEY, value)
        retrieved_value = cache_instance.get(_KEY)

        assert retrieved_value == value
        assert isinstance(retrieved_value, dict)
        assert retrieved_value["key"] == "value"
        assert retrieved_value["number"] == 42

    def test_close(self, patch_redis):
        cache_instance = RedisCache(RedisUrl(base_url="unused"))
        cache_instance.close()
        assert patch_redis.closed


class TestFakeCache:

    def test_set_and_get(self):
        cache_instance = cache.FakeCache[str, str]()

        cache_instance.set(_KEY, _VALUE)

        assert cache_instance.get(_KEY) == _VALUE

    def test_has_key(self):
        cache_instance = cache.FakeCache[str, str]()

        assert not cache_instance.has(_KEY)
        cache_instance.set(_KEY, _VALUE)
        assert cache_instance.has(_KEY)
    
    def test_get_non_existent_key(self):
        cache_instance = cache.FakeCache[str, str]()
        assert cache_instance.get(_KEY) is None
