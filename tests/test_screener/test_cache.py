from typing import Any

import redis
import pydantic
import pytest

from screener.core import cache


Cache = cache.Cache
RedisUrl = cache.RedisUrl


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


class TestCache:

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
        cache_instance = Cache(RedisUrl(base_url="unused"))
        key = "test_key"
        value = "test_value"

        cache_instance.set(key, value)

        assert cache_instance.get(key) == value

    def test_get_with_different_namespaces(self, patch_redis):
        url = RedisUrl(base_url="unused")
        cache1 = Cache(url, namespace="ns1")
        cache2 = Cache(url, namespace="ns2")
        common_key = "key"
        cache1_value = "val"
        cache2_value = "a_different_val"
        cache1.set(common_key, cache1_value)
        cache2.set(common_key, cache2_value)

        assert cache1.get(common_key) == cache1_value
        assert cache2.get(common_key) == cache2_value

    def test_has_key(self, patch_redis):
        cache_instance = Cache(RedisUrl(base_url="unused"))
        key = "test_key"
        value = "test_value"

        assert not cache_instance.has(key)
        cache_instance.set(key, value)
        assert cache_instance.has(key)

    def test_get_non_existent_key(self, patch_redis):
        cache_instance = Cache(RedisUrl(base_url="unused"))
        key = "non_existent_key"
        assert cache_instance.get(key) is None

    def test_set_and_get_pickleable_object(self, patch_redis):
        cache_instance = Cache(RedisUrl(base_url="unused"))
        key = "test_object"
        value = {"key": "value", "number": 42}

        cache_instance.set(key, value)
        retrieved_value = cache_instance.get(key)

        assert retrieved_value == value
        assert isinstance(retrieved_value, dict)
        assert retrieved_value["key"] == "value"
        assert retrieved_value["number"] == 42

    def test_close(self, patch_redis):
        cache_instance = Cache(RedisUrl(base_url="unused"))
        cache_instance.close()
        assert patch_redis.closed
