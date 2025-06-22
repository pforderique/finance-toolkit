import json
import os
from typing import Any


class Cache:
    """Cache for storing API responses.

    Stores the API responses in a JSON file to avoid making
    unnecessary API calls. Calls are stored in the format:
    {
        "route": {
            "request": response
        }
    }
    """

    FILEPATH = "data/api_calls_cache.json"

    def get(self, route, request=None) -> Any | None:
        """Get value from cache."""
        if route not in self.cache:
            return None
        if request is None:
            return self.cache[route]
        return self.cache[route].get(request, None)

    def set(self, response, route, request=None, skip_write=False) -> None:
        """Set value in cache."""
        if route in self.cache and request is not None:
            self.cache[route][request] = response
        else:
            self.cache[route] = {request: response}

        if not skip_write:
            self.save()

    def save(self) -> None:
        """Save cache to file."""
        os.makedirs(os.path.dirname(Cache.FILEPATH), exist_ok=True)
        with open(Cache.FILEPATH, "w") as f:
            json.dump(self.cache, f, indent=4, default=str)

    @classmethod
    def load(cls) -> "Cache":
        """Load cache from file."""
        if os.path.exists(cls.FILEPATH):
            with open(cls.FILEPATH, "r") as f:
                cls.cache = json.load(f)
        else:
            cls.cache = {}
        return cls()
