import threading
import time
import pytest

from screener.core.rate_limiter import RateLimiter


class TestRateLimiter:

    def test_invalid_max_calls(self):
        with pytest.raises(ValueError):
            RateLimiter(0)

    def test_nonblocking_wait(self):
        rate_limiter = RateLimiter(max_calls=2, period=1.0)
        assert rate_limiter.wait() == 0.0
        assert rate_limiter.wait() == 0.0
        assert rate_limiter.wait() > 0.0

    def test_blocking_wait(self):
        rate_limiter = RateLimiter(max_calls=1, period=0.01)
        rate_limiter.wait()

        start = time.time()
        wait_time = rate_limiter.wait(blocking=True)
        end = time.time()
        assert end - start >= wait_time

    def test_concurrent_calls(self):
        rate_limiter = RateLimiter(max_calls=1, period=1.0)

        worker = lambda: rate_limiter.wait(blocking=False)
        threads = [threading.Thread(target=worker) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # 5 threads with non-blocking calls will have exceeded the max_calls
        # limit for the period, so current waits should be greater than the
        # 4*period to ensure they were spaced out appropriately
        wait_time = rate_limiter.wait(blocking=False)
        assert wait_time > 4*rate_limiter.period

    def test_wait_after_period(self):
        rate_limiter = RateLimiter(max_calls=1, period=1e-5)
        rate_limiter.wait()
        time.sleep(1e-4)

        assert rate_limiter.wait() == 0.0
