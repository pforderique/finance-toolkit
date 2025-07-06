"""
Cache abstraction backed by Redis for storing arbitrary Python objects under
string keys. Supports multiple namespaces.
"""

import pickle
from typing import Any, Self

import pydantic
import redis


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


class Cache:
    """
    Simple Redis-backed cache. Stores pickled Python objects under string keys.

    Args:
        url: Redis connection URL (e.g., redis://localhost:6379/0)
        namespace: Optional key prefix to isolate this cache (e.g. "api_cache").
    """

    def __init__(self, url: RedisUrl, namespace: str = ""):
        self._namespace = namespace.strip(':')
        self._client = redis.Redis.from_url(
            url.get_full_url(),
            decode_responses=True
        )

    def _prefixed(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    def get(self, key: str) -> Any | None:
        """Retrieve a value by key, or None if not present."""
        raw: Any = self._client.get(self._prefixed(key))

        if raw is None:
            return None

        return pickle.loads(raw)

    def set(self, key: str, val: Any) -> Self:
        """Serialize and store a Python object under the given key.

        Args:
            key: The string key to store the value under.
            val: The Python object to store (must be pickleable).
        """
        raw = pickle.dumps(val)
        self._client.set(self._prefixed(key), raw)
        return self

    def has(self, key: str) -> bool:
        """Return True if the key exists in cache."""
        return self._client.exists(self._prefixed(key)) == 1

    def close(self) -> Self:
        """Close the Redis connection cleanly."""
        self._client.close()
        return self
