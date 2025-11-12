# Morningstar Screener

## Usage

**COMMAND:**

```bash
python ms_screener/main.py
```

Run the auto-collector (logs in through SPL EZProxy, opens
Morningstar comparison links, and downloads the CSVs) with:

```bash
python ms_screener/main.py --auto
```

**DATA PREREQUISITES:**

Reads in `collected_data` tab from an access-only `Morningstar Screener` Google
Sheet, containing the ticker, performance ID, uncertainty, and ratings date for
various securities.

**INPUT:**

Generates direct Morningstar links to comparison pages with a CSV download
option.

**OUT:**

Writes the processed data to a new Google Sheet with computed fields like
`fair_value`, `moat`, and `ratings`, used by manual trading strategies.

## Automation requirements

- Set `SPL_BARCODE` and `SPL_PIN` in `ms_screener/.env` (or your shell env) so the Selenium
  session can authenticate against the SPL EZProxy login screen.
- Chrome must be installed. If `chromedriver` is not already on your `PATH`, either set
  `CHROMEDRIVER=/path/to/chromedriver` or allow `webdriver-manager` to fetch a matching
  driver on first run.
- Pass `--auto-visible` if you want to watch the browser session instead of running headless.


## Future Improvements

- Automate data collection from Morningstar to eliminate manual input.
- Display new changes in FMV and ratings since the last update.
- Integrate with a database for FMV and rating tracking over time.
- Automated BUY/SELL/HOLD recommendations based on computed fields.
