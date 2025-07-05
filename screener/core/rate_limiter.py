"""Rate limiter using a fixed-size min-heap to track timestamps of API calls."""

import threading
import time
import datetime

from screener.core.heap import KHeap


class RateLimiter:
    """
    Thread-safe rate limiter tracking the timestamps of the last `max_calls`
    events. Each call to `wait()` returns how many seconds the caller should
    wait before making the next API call, and records that call immediately.

    Example usage:
    >>> rate_limiter = RateLimiter(max_calls=1, period=1.0)
    >>> rate_limiter.wait()
    0.0
    >>> rate_limiter.wait() # Should return a positive time if called within 1s
    """

    def __init__(self, max_calls: int, period: float = 1.0):
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")

        self.max_calls = max_calls
        self.period = period
        self._heap = KHeap[float](k=max_calls, is_min_heap=True)
        self._lock = threading.Lock()

    def wait(self, blocking=False) -> float:
        """
        Calculates how long to wait before the next call in order to not exceed
        `max_calls` in any rolling window of length `period`.

        Args:
            blocking: If True, will block until the next call can be made.

        Returns:
            The number of seconds to wait (or waited) before the next call.
        """
        with self._lock:
            now = datetime.datetime.now()
            if self._heap.size() < self.max_calls:
                wait = 0.0
                scheduled = now
            else:
                earliest = self._heap.top()
                wait = max(0.0, (earliest + self.period) - now.timestamp())
                scheduled = now + datetime.timedelta(seconds=wait)

            self._heap.push(scheduled.timestamp())

        if blocking and wait > 0:
            time.sleep(wait)

        return wait
