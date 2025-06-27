from src import morningstar as ms
from src import common
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


_NUM_THREADS = 4 if len(common.STOCKS) >= 20 else 2 if len(common.STOCKS) >= 10 else 1
print(f"USING {_NUM_THREADS} THREADS")


def fetch_stock_data(ms_api: ms.MoringstarAPI, stock: str):
    start_time = time.time()
    try:
        data = ms_api.get_stock_data(stock, force_update=True)
    except requests.RequestException:
        data = None
        print(f"could not complete stock {stock}")
    elapsed_time = time.time() - start_time
    return stock, data, elapsed_time


if __name__ == "__main__":
    ms_api = ms.MoringstarAPI.get_instance()
    speeds = []
    incomplete_stocks = []

    start_program_time = time.time()
    with ThreadPoolExecutor(max_workers=_NUM_THREADS) as executor:
        futures = {executor.submit(fetch_stock_data, ms_api, stock): stock for stock in common.STOCKS}
        for future in as_completed(futures):
            stock, data, elapsed_time = future.result()
            speeds.append(elapsed_time)
            if data is None:
                incomplete_stocks.append(stock)
            print(f"({elapsed_time:.2f}s) {data}")

    total_program_time = time.time() - start_program_time
    print(f"Total stocks: {len(common.STOCKS)}")
    print(f"{len(incomplete_stocks)} stocks failed: {incomplete_stocks}")
    print(f"Total synchronous time: {sum(speeds):.2f}s ({sum(speeds) / len(common.STOCKS):.2f}s/stock)")
    print(f"Total asynchronous time: {total_program_time:.2f}s ({total_program_time / len(common.STOCKS):.2f}s/stock)")
