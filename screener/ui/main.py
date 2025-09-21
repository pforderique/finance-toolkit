"""Main entry point for the Stock Screener UI application."""

import argparse
from collections.abc import Collection
import logging
import textwrap

import npyscreen

from screener import config
from screener.service import update_stocks
from screener.service import stock_api
from screener.ui import screen
from screener.ui import stock_screener
from screener.ui import home


CacheOption = stock_api.CacheOption
HomeScreen = home.HomeScreen
Screen = screen.Screen
StockAPI = stock_api.StockAPI
StockScreener = stock_screener.StockScreener

_api_cache = StockAPI().stock_cache
_root = logging.getLogger("screener.ui.main")


_YES_OPTIONS = ("y", "yes", "")
_UPDATE_STOCKS_TEMPLATE = textwrap.dedent( """
python -m screener.service.update_stocks \
    --symbols={symbols} \
    --cache-option={cache_option.name}
""")


def _wait_for_all_updates(
        symbols: Collection[str], cache_option: CacheOption) -> Collection[str]:
    """Wait for all stock updates to complete and return failed symbols."""
    failed_updates = set()
    for result in update_stocks.update_stock_data(symbols, cache_option):
        if result.data is None:
            failed_updates.add(result.symbol)

    if failed_updates:
        _root.error("Failed to update cache for symbols: %s.", failed_updates)

    return failed_updates


def ensure_cache(
    symbols: Collection[str],
    auto_approve: bool,
    cache_option: CacheOption,
) -> bool:
    """
    Ensure all symbols are in cache before starting the UI.
    If any symbols are missing, prompt to update the cache.
    """

    if cache_option in (CacheOption.REFRESH_ALL, CacheOption.REFRESH_PRICE_ONLY):
        if cache_option == CacheOption.REFRESH_ALL:
            prompt = f"Refresh all {len(symbols)} symbols? (Y/n): "
            log_msg = "Refreshing all caches for symbols: %s"
        else:
            prompt = f"Refresh prices for {len(symbols)} symbols? (Y/n): "
            log_msg = "Refreshing prices for symbols: %s"

        if auto_approve or input(prompt).strip().lower() in _YES_OPTIONS:
            _root.info(log_msg, symbols)
            _root.info("Running command: %s", _UPDATE_STOCKS_TEMPLATE.format(
                symbols=symbols, cache_option=cache_option))
            _wait_for_all_updates(symbols, cache_option)
            return True

        return False

    if not (missing := [s for s in symbols if not _api_cache.has(s)]):
        return True

    prompt = f"Missing {len(missing)} symbols: {missing}\nFetch now? (Y/n): "

    if auto_approve or input(prompt).strip().lower() in _YES_OPTIONS:
        _root.info("Updating cache for missing symbols: %s", missing)
        _root.info("Running command: %s", _UPDATE_STOCKS_TEMPLATE.format(
            symbols=missing, cache_option=cache_option))
        _wait_for_all_updates(missing, cache_option)
        return True

    return False


class UI(npyscreen.NPSAppManaged):
    """Main application class for the Stock Screener UI."""

    def __init__(self, symbols: Collection[str]):
        super().__init__()
        self.symbols = symbols

    def onStart(self):
        self.addForm(Screen.MAIN, HomeScreen)
        self.addForm(Screen.STOCK_SCREENER, StockScreener)


def main(args: argparse.Namespace) -> int:
    """Main entry point for the Stock Screener UI app."""

    symbols = set()
    for sym in args.stocks.split(","):
        symbol = sym.strip().upper()
        if symbol in symbols:
            _root.warning("Duplicate symbol '%s' found in input", symbol)
        symbols.add(symbol)

    if not ensure_cache(symbols, args.auto_approve, args.cache_option):
        _root.exception(
            "Failed to ensure cache for symbols: %s. Exiting.", symbols)
        return 1

    app = UI(symbols)
    app.run()
    return 0


if __name__ == "__main__":
    LOG_LEVEL = config.LOG_LEVEL
    _root.setLevel(LOG_LEVEL)
    logging.getLogger("screener.core.api_client").setLevel(LOG_LEVEL)
    logging.getLogger("screener.service.stock_api").setLevel(logging.DEBUG)
    logging.getLogger("screener.service.update_stocks").setLevel(LOG_LEVEL)

    for h in _root.handlers[:]:
        _root.removeHandler(h)

    console = logging.StreamHandler()
    console.setLevel(LOG_LEVEL)
    console.setFormatter(logging.Formatter(
        "%(asctime)s|Screener_UI|%(levelname)s: %(message)s"
    ))
    console.addFilter(logging.Filter("screener"))
    _root.addHandler(console)

    p = argparse.ArgumentParser()
    p.add_argument(
        "-c", "--cache-option",
        type=lambda s: CacheOption[s.upper()],
        choices=list(CacheOption),
        default=CacheOption.CHECK_ALL,
        help="Cache option for stock data retrieval (default: CHECK_ALL)"
    )
    p.add_argument(
        "-s", "--stocks",
        type=str,
        default=",".join(config.WATCHLIST),
        help="Comma-separated list of stock symbols to display (default: config.WATCHLIST)"
    )
    p.add_argument(
        "-y", "--auto-approve",
        action="store_true",
        help="Auto-approve cache update commands without prompt"
    )

    main(p.parse_args())
