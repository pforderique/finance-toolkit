# Stock Screener

This is an aside project - it is a stock screener that shows Moringstar Fair Market Values, star ratings, and discount.
The table also reccommends which stocks to BUY, HOLD, or SELL based off these ratings.

In the future, it will connect with a real-life implementation of the Exchange (pending funding) to stream current prices of securities.

---

stock_screener/
├── config.py
├── watchlist.py
├── core/
│   ├── heap.py
│   ├── rate_limiter.py
│   ├── cache.py
│   ├── api.py
│   └── stock_api.py
├── updater/
│   └── update_cache.py
├── ui/
│   └── main.py
├── scripts/
│   └── cron_job.sh
│
├── tests/
│   ├── test_watchlist.py
│   ├── core/
│   │   ├── test_heap.py
│   │   ├── test_rate_limiter.py
│   │   ├── test_cache.py
│   │   └── test_api.py
│   ├── updater/
│   │   └── test_update_cache.py
│   └── ui/
│       └── test_main.py
│
├── pytest.ini
├── tox.ini              # optional: define envs for lint, pytest, coverage
├── .github/workflows/ci.yml  # run pytest + coverage on push/PR
└── requirements.txt

---