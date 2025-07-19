"""Cache abstraction layer for Python applications.

This module provides an abstract base class for cache implementations including
a Redis-backed cache with namespace support and a FakeCache for testing.
"""

import abc
import json
from typing import Any, Generic, Self, TypeVar
import urllib.parse

import pydantic
import redis
import redis.exceptions


K = TypeVar('K')
V = TypeVar('V')


class Cache(abc.ABC, Generic[K, V]):
    """Abstract base class for cache implementations."""

    @abc.abstractmethod
    def get(self, key: K) -> V | None:
        """Retrieve a value by key, or None if not present."""

    @abc.abstractmethod
    def set(self, key: K, val: V) -> Self:
        """Store a value under the given key."""

    @abc.abstractmethod
    def has(self, key: K) -> bool:
        """Return True if the key exists in cache."""

    @abc.abstractmethod
    def ping(self) -> bool:
        """Return True if the cache is reachable."""

    @abc.abstractmethod
    def close(self) -> Self:
        """Close the cache connection cleanly."""


class RedisUrl(pydantic.BaseModel):
    """
    Model for validating Redis connection URLs.

    Attributes:
        base_url: Hostname or IP address of the Redis server (e.g., "localhost").
        port: Port number for the Redis server (default is 6379).
        db: Database index to use (default is 0).
    """
    base_url: str
    port: int = 6379
    db: int = 0

    @classmethod
    def parse_url(cls, url: str) -> Self:
        """Parse a Redis URL string into a RedisUrl instance.

        Args:
            url: Redis connection URL in the format redis://{base_url}:{port}/{db}

        Returns:
            RedisUrl: Parsed RedisUrl instance.

        Raises:
            ValueError: If the URL scheme is not 'redis' or if the URL is invalid
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'redis':
            raise ValueError("Invalid Redis URL scheme, must be 'redis'")
        return cls(
            base_url=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            db=int(parsed.path[1:]) if parsed.path else 0
        )

    def get_full_url(self) -> str:
        """Construct the full Redis URL from components.

        Returns:
            str: Full Redis URL in the format redis://{base_url}:{port}/{db}

        Example:
            >>> redis_url = RedisUrl(base_url="localhost", port=6379, db=0)
            >>> redis_url.get_full_url()
            "redis://localhost:6379/0"
        """
        return f"redis://{self.base_url}:{self.port}/{self.db}"

    @pydantic.field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """Validate the base URL for Redis connection."""
        if not value:
            raise ValueError("Base URL cannot be empty")
        return value.strip()

    @pydantic.field_validator("db")
    @classmethod
    def validate_db(cls, value: int) -> int:
        """Validate the database index."""
        if value < 0:
            raise ValueError("Database index must be non-negative")
        return value

    def __str__(self) -> str:
        return self.get_full_url()


class RedisCache(Cache[str, Any]):
    """
    Simple Redis-backed cache. Stores JSON-serialized Python objects under string keys.

    Args:
        url: Redis connection URL (e.g., redis://localhost:6379/0)
        namespace: Optional key prefix to isolate this cache (e.g. "api_cache").
    """

    def __init__(self, client: redis.Redis, namespace: str = ""):
        self._client = client
        self._namespace = namespace.strip(':')

    def _prefixed(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    @classmethod
    def from_url(cls, url: RedisUrl, namespace: str = "") -> Self:
        """Create a RedisCache instance from a RedisUrl."""
        return cls(redis.Redis.from_url(url.get_full_url()), namespace)

    @classmethod
    def from_client(cls, client: redis.Redis, namespace: str = "") -> Self:
        """Create a RedisCache instance from an existing Redis client."""
        return cls(client, namespace)

    def get(self, key: str) -> Any | None:
        """Retrieve a value by key, or None if not present."""
        raw: Any = self._client.get(self._prefixed(key))

        if raw is None:
            return None

        return json.loads(raw)

    def set(self, key: str, val: Any) -> Self:
        """Serialize and store a JSON-serializable Python object under the given key.

        Args:
            key: The string key to store the value under.
            val: The Python object to store (must be JSON-serializable).
        """
        raw = json.dumps(val)
        self._client.set(self._prefixed(key), raw)
        return self

    def has(self, key: str) -> bool:
        """Return True if the key exists in cache."""
        return self._client.exists(self._prefixed(key)) == 1

    def ping(self) -> bool:
        try:
            self._client.ping()
        except redis.exceptions.ConnectionError:
            return False
        return True

    def close(self) -> Self:
        """Close the Redis connection cleanly."""
        self._client.close()
        return self


class FakeCache(Cache[K, V], Generic[K, V]):
    """In-memory fake cache for testing purposes.

    Implements same interface as Cache.
    """

    def __init__(self):
        self._store: dict[K, V] = {}

    def get(self, key: K) -> V | None:
        return self._store.get(key)

    def set(self, key: K, val: V) -> Self:
        self._store[key] = val
        return self

    def has(self, key: K) -> bool:
        return key in self._store

    def ping(self) -> bool:
        return True

    def close(self) -> Self:
        return self
