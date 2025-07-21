# 📈 Stock Screener

A command‐line & terminal‐UI tool to fetch, cache, and screen stocks via the
Morningstar API.  
It keeps a local Redis cache, respects rate limits, rotates API keys on quota
errors, and can send alert emails.

Contains:
* **update_stocks.py**: utility & script to monitor daily price changes and
ratings update. Can send alert emails with actions per stock.
* **terminal UI:** for quickly screening stocks based on their fair market
value, discount/premium, and ratings.

## ⚙️ Software Requirements
Make sure you have the following:
- [**Python**](https://www.python.org/downloads/) (3.11+)
- [**Docker**](https://docs.docker.com/desktop/setup/install/mac-install/)
- At least one valid Morningstar key
- (optional) A Gmail (or SMTP) account to send alerts

## One-Time Setup

1. **install project**<br>
First, make sure that you've [installed](../README.md#installation) the
finance-toolkit project.

2. **configure environment**<br>
Edit [`screener/config.py`](./config.py) if needed. Ideally, most should be
kept the same, and you should only really need to add a `screener/.env` file
with the following:
```bash
# Must include
MORNINGSTAR_API_BASE_URL=<moriningstar_base_url>
MORNINGSTAR_API_KEYS=<comma,separated,api_keys>

# Only include if planning on using alerting functionality
EMAIL_USERNAME=<email_address_of_sender>
EMAIL_PASSWORD=<email_app_password>
ALERT_EMAILS=<comma,separated,email_addresses>
```

| **Note:** an
[app password](https://support.google.com/mail/answer/185833?hl=en) is needed to
sign in to the gmail account of the sender.

3. **start redis**<br>
The scripts and UI depend on redis to store results from the api. The folowing
script starts a redis server on your machine on port 6379. 
```bash
bash ./scripts/start-redis.sh
```

4. **(optional) stop redis**<br>
After you've ran the scripts and UI, clean up the redis server by running:
```bash
bash ./scripts/stop-redis.sh
```

## 🚀 Usage
1. Update & cache stock data
```bash
# Fetch stock info, refresh cache, and send alerts if configured:
python -m screener.service.update_stocks \
  --stocks=AAPL,MSFT,GOOG \
  --cache-option=REFRESH_PRICE_ONLY \
  --alert
```

**Flags**
* --stocks (-s): comma-separated list of symbols (default: your watchlist).
* --cache-option (-c):
  - CHECK_ALL: use cached responses for all requests
  - REFRESH_ALL: update cached values for all requests
  - REFRESH_PRICE_ONLY: only update cache price values (reccommended most times)
* --alert (-a): if set, it will send an email with actions for each stock.

