"""Constants for the Fidelity portfolio sync tool."""

from pathlib import Path

# Application metadata
APP_NAME = "fidelity-portfolio-sync"

# Environment variable keys
ENV_SHEET_ID = "SHEET_ID"
ENV_SERVICE_ACCOUNT_JSON = "GOOGLE_SERVICE_ACCOUNT_JSON"
ENV_CREDENTIALS_PATH = "GOOGLE_APPLICATION_CREDENTIALS"

# Google Sheets configuration
PORTFOLIO_TAB = "Portfolio_Tracker"
SHEET_RANGE_START_ROW = 3
SHEET_RANGE_COLUMNS = ["A", "B", "C", "D", "E", "F", "G"]
SHEET_RANGE_NAMES = [
    "Ticker",
    "Shares",
    "Avg. Cost",
    "Market Price",
    "Total Equity",
    "Percent Gain",
    "Account",
]

# Local filesystem layout
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
ARTIFACTS_DIR = ROOT_DIR / "out"
LOG_DIR = ROOT_DIR / "logs"

# Column names used across the tool
CSV_ACCOUNT_NAME = "Account Name"
CSV_SYMBOL = "Symbol"
CSV_QUANTITY = "Quantity"
CSV_AVG_COST = "Average Cost Basis"
CSV_COST_BASIS_TOTAL = "Cost Basis Total"
CSV_DESCRIPTION = "Description"

SHEET_TICKER_COL = "Ticker"
SHEET_SHARES_COL = "Shares"
SHEET_AVG_COST_COL = "Avg. Cost"
SHEET_ACCOUNT_COL = "Account"
SHEET_DESCRIPTION_COL = "Description"

# Account label mapping
ACCOUNT_NAME_TO_SHEET_LABEL = {
    "Individual": "Fidelity Brokerage",
    "Brokerage": "Fidelity Brokerage",
    "ROTH IRA": "Fidelity Roth IRA",
    "Roth IRA": "Fidelity Roth IRA",
    "Health Savings Account": "Fidelity HSA",
    "HSA": "Fidelity HSA",
    "BROAD INSTITUTE 401K": "Fidelity Broad 401K",
}

# Symbols to omit by prefix (upper-case)
OMIT_SYMBOL_PREFIXES = ("SPAXX", "FDRXX")
OMIT_SYMBOL_VALUES = {"PENDING ACTIVITY"}

# CSV validation
REQUIRED_CSV_COLUMNS = {
    CSV_ACCOUNT_NAME,
    CSV_SYMBOL,
    CSV_QUANTITY,
    CSV_AVG_COST,
}

# Logging
DEFAULT_LOG_LEVEL = "INFO"
