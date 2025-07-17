"""This module contains configuration settings for the stock screener application.

It includes API settings, rate limiting, caching, watchlist management, and logging configurations.
"""

import os
import sys

from dotenv import load_dotenv


load_dotenv()

# ─── API Settings ─────────────────────────────────────────────────────────────

MORNINGSTAR_API_BASE_URL: str = os.getenv("MORNINGSTAR_API_BASE_URL", "")

MORNINGSTAR_API_KEYS: list[str] = [
    key.strip() for key in os.getenv("MORNINGSTAR_API_KEYS", "").split(",")
]

MORNINGSTAR_API_TIMEOUT: float = 10.0  # seconds
MORNINGSTAR_API_MAX_RETRIES: int = 3

# ─── Rate Limiter Settings ────────────────────────────────────────────────────

# Max requests per second
RATE_LIMIT_PER_SECOND: int = 5

# Max requests per day
RATE_LIMIT_PER_DAY: int = 500

# ─── Cache Settings ───────────────────────────────────────────────────────────

REDIS_URL: str = "redis://localhost:6379/0"

# ─── Alerting ─────────────────────────────────────────────────────────────────

ALERT_EMAILS: list[str] = [
    email.strip() for email in os.getenv("ALERT_EMAILS", "").split(",")
]

# ─── Logging & Misc ───────────────────────────────────────────────────────────

LOG_LEVEL: str = "INFO"   # e.g. DEBUG, INFO, WARNING

# ─── Watchlist ────────────────────────────────────────────────────────────────

WATCHLIST = [
    "AAPL",
    "ADI",
    "AMAT",
    "AMZN",
    "AVGO",
    "AXP",
    "BA",
    "BKNG",
    "BLK",
    "BRK.B",
    "BX",
    "CAVA",
    "CMG",
    "COKE",
    "COR",
    "COST",
    "CRBP",
    "CRWD",
    "DAL",
    "DELL",
    "DIS",
    "EPD",
    "FSLR",
    "GOOG",
    "GOOGL",
    "HOOD",
    "HSY",
    "INC",
    "INTC",
    "IONQ",
    "IQV",
    "KO",
    "LCID",
    "LLY",
    "LNW",
    "MCD",
    "MCK",
    "META",
    "MSFT",
    "MU",
    "NFLX",
    "NLY",
    "NOC",
    "NVDA",
    "O",
    "PANW",
    "PFE",
    "QCOM",
    "RIVN",
    "SPGI",
    "TGT",
    "TSCO",
    "TSLA",
    "UAL",
    "UBER",
    "UNH",
    "V",
    "WM",
    "WMT",
    "ZTS",
]

# ─── Validation ───────────────────────────────────────────────────────────────

def validate_config():
    """Validate configuration settings."""
    if not MORNINGSTAR_API_BASE_URL:
        raise ValueError("MORNINGSTAR_API_BASE_URL is not set.")
    if not MORNINGSTAR_API_KEYS:
        raise ValueError("MORNINGSTAR_API_KEYS is not set.")
    if RATE_LIMIT_PER_SECOND <= 0:
        raise ValueError("RATE_LIMIT_PER_SECOND must be greater than 0.")
    if RATE_LIMIT_PER_DAY <= 0:
        raise ValueError("RATE_LIMIT_PER_DAY must be greater than 0.")
    if not REDIS_URL:
        raise ValueError("REDIS_URL is not set.")

    print("Configuration validation passed.")
    print(f"MORNINGSTAR_API_BASE_URL: {MORNINGSTAR_API_BASE_URL}")
    print(f"MORNINGSTAR_API_KEYS: {','.join(MORNINGSTAR_API_KEYS)}")
    print(f"RATE_LIMIT_PER_SECOND: {RATE_LIMIT_PER_SECOND}")
    print(f"RATE_LIMIT_PER_DAY: {RATE_LIMIT_PER_DAY}")
    print(f"REDIS_URL: {REDIS_URL}")

if __name__ == "__main__":
    try:
        validate_config()
        print("Configuration is valid.")
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
