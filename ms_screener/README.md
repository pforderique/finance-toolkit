# Morningstar Screener

## Usage

**COMMAND:**

```bash
python ms_screener/main.py
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


## Future Improvements

- Automate data collection from Morningstar to eliminate manual input.
- Display new changes in FMV and ratings since the last update.
- Integrate with a database for FMV and rating tracking over time.
- Automated BUY/SELL/HOLD recommendations based on computed fields.
