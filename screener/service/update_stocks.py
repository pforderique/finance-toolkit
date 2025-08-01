"""Update stock data in parallel and optionally send alerts for stocks"""

import argparse
from collections.abc import Iterable
from concurrent import futures
import dataclasses
import logging
import pathlib
import sys
import subprocess
import time

import redis.exceptions
import requests
import validators

from screener import config
from screener.service import alerting
from screener.service import stock_api


CacheOption = stock_api.CacheOption
MSAPIKeysExhaustedError = stock_api.MSAPIKeysExhaustedError
Path = pathlib.Path
StockAPI = stock_api.StockAPI
StockInfo = stock_api.StockInfo

_root_logger = logging.getLogger("screener.service.update_stocks")
_screener_dir = Path(__file__).resolve().parent.parent


@dataclasses.dataclass
class UpdateResult:
    """Result of updating a stock symbol."""
    symbol: str
    data: StockInfo | None
    duration: float


def update_stock_data(
    symbols: Iterable[str],
    cache_option: CacheOption = CacheOption.REFRESH_ALL,
    max_workers: int | None = None
) -> Iterable[UpdateResult]:
    """
    Refresh the cache for each symbol in parallel, yielding (symbol, data)
    where data is None if a symbol-based error occurred.

    Args:
        symbols: Iterable of stock symbols to update.
        max_workers: Maximum number of parallel workers.
            If None, defaults to the number of CPUs.

    Returns:
        Iterable of UpdateResult objects containing symbol, data, and duration.

    Raises:
        MSAPIKeysExhaustedError: If all API keys are exhausted.
        redis.exceptions.ConnectionError: If Redis cache is not reachable.
    """
    api = StockAPI()

    def work(sym: str) -> UpdateResult:
        t0 = time.perf_counter()
        try:
            data = api.get_info(sym, cache_option=cache_option)
        except (
            requests.exceptions.RetryError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError
        ) as e:
            _root_logger.error("Retry or timeout occurred for %s: %s", sym, e)
            data = None
        except (
            MSAPIKeysExhaustedError,
            redis.exceptions.ConnectionError,
        ) as e:
            _root_logger.critical("Cannot not proceed %s: %s", sym, e)
            raise e
        finally:
            dt = time.perf_counter() - t0
        return UpdateResult(sym, data, dt)

    with futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        _futures = {executor.submit(work, sym): sym for sym in symbols}
        for fut in futures.as_completed(_futures):
            yield fut.result()


def _validate_symbols(symbols: Iterable[str]) -> set[str]:
    """Perform mini validation and return unique stock symbols."""
    valid_symbols = set()
    for sym in symbols:
        if isinstance(sym, str) and len(sym) <= 5:
            if sym in valid_symbols:
                _root_logger.warning("Duplicate symbol found: %s", sym)
            valid_symbols.add(sym)
        else:
            _root_logger.warning("Invalid stock symbol: %s (skipped)", sym)
    return valid_symbols


def _validate_emails(emails: Iterable[str]) -> set[str]:
    """Validate email addresses and return a set of unique valid emails."""
    valid_emails = set()
    for email in emails:
        if not validators.email(email):
            _root_logger.warning("Invalid email address: %s (skipped)", email)
            continue

        if email in valid_emails:
            _root_logger.warning("Duplicate email found: %s", email)
        else:
            valid_emails.add(email)

    return valid_emails


def _call_redis_script(script: Path) -> bool:
    """Call a Redis script and return True if successful, False otherwise."""
    process = subprocess.run(["bash", str(script)], check=True)
    return process.returncode == 0


def main(args: argparse.Namespace) -> int:
    """Main entry point to update stock data and optionally send alerts."""
    _root_logger.info(
        "Starting stock data update with cache option: %s",
        args.cache_option.name
    )

    _root_logger.info("Starting Redis cache...")
    start_redis_script = str(_screener_dir / "scripts" / "start-redis.sh")
    if not _call_redis_script(Path(start_redis_script)):
        _root_logger.critical("Failed to start Redis cache. Exiting.")
        return 1

    if not (unique_symbols := _validate_symbols(args.stocks.split(","))):
        _root_logger.error("No symbols provided.")
        return 1

    _root_logger.info("Updating stock data for %d symbols: %s",
                      len(unique_symbols), unique_symbols)

    failed_symbols: set[str] = set()
    stock_infos: list[StockInfo] = []
    speeds: list[float] = []

    start = time.perf_counter()
    for result in update_stock_data(
        unique_symbols, cache_option=args.cache_option, max_workers=args.workers
    ):
        if result.data is None:
            failed_symbols.add(result.symbol)
            _root_logger.warning("[%s] failed to update data", result.symbol)
        else:
            speeds.append(result.duration)
            stock_infos.append(result.data)
            _root_logger.debug("[%s] updated in %.2fs",
                               result.symbol, result.duration)

    end = time.perf_counter()

    elapsed = end - start
    synchronous_time = sum(speeds)
    _root_logger.info("-" * 40)
    _root_logger.info("Total stocks processed: %d", len(unique_symbols))
    _root_logger.info("Failed to update %d stocks: %s",
                      len(failed_symbols), failed_symbols)
    _root_logger.info("Total synchronous time: %.2fs (%.2fs/stock)",
                      synchronous_time,
                      synchronous_time / len(speeds) if speeds else 0)
    _root_logger.info("Total asynchronous time: %.2fs (%.2fs/stock)",
                      elapsed, elapsed / len(speeds) if speeds else 0)

    _root_logger.info("Stopping Redis cache...")
    stop_redis_script = str(_screener_dir / "scripts" / "stop-redis.sh")
    if not _call_redis_script(Path(stop_redis_script)):
        _root_logger.warning("Failed to stop Redis cache.")

    if not args.alert:
        return 0

    if (alert_emails := _validate_emails(config.ALERT_EMAILS)):
        _root_logger.info(
            "sending alerts for %s to %s",
            [s.ticker for s in stock_infos if s is not None],
            alert_emails
        )
        alerting.send_alerts(alert_emails, stock_infos, failed_symbols)
    else:
        _root_logger.warning(
            "no valid emails found in config.ALERT_EMAILS. skipping.")

    return 0


if __name__ == "__main__":
    LOG_LEVEL = config.LOG_LEVEL
    _root_logger.setLevel(LOG_LEVEL)
    logging.getLogger("screener.core.api_client").setLevel(LOG_LEVEL)
    logging.getLogger("screener.service.stock_api").setLevel(LOG_LEVEL)

    for h in _root_logger.handlers[:]:
        _root_logger.removeHandler(h)

    console = logging.StreamHandler()
    console.setLevel(LOG_LEVEL)
    console.setFormatter(logging.Formatter(
        "%(asctime)s|update_stocks|%(levelname)s: %(message)s"
    ))
    console.addFilter(logging.Filter("screener"))
    _root_logger.addHandler(console)

    p = argparse.ArgumentParser()
    p.add_argument(
        "-a", "--alert",
        action="store_true",
        help="Send alerts for stocks to config.ALERT_EMAILS"
    )
    p.add_argument(
        "-c", "--cache-option",
        type=lambda s: CacheOption[s.upper()],
        choices=list(CacheOption),
        default=CacheOption.REFRESH_ALL,
        help="Cache option for stock data retrieval (default: REFRESH_ALL)"
    )
    p.add_argument(
        "-s", "--stocks",
        type=str,
        default=",".join(config.WATCHLIST),
        help="Comma-separated list of stock symbols to update (default: config.WATCHLIST)"
    )
    p.add_argument(
        "-w", "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: CPU count)"
    )

    sys.exit(main(p.parse_args()))
