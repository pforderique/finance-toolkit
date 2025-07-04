"""This module contains configuration settings for the stock screener application.

It includes API settings, rate limiting, caching, watchlist management, and logging configurations.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ─── API Settings ─────────────────────────────────────────────────────────────

MORNINGSTAR_API_BASE_URL: str = os.getenv("MORNINGSTAR_API_BASE_URL")

if not MORNINGSTAR_API_BASE_URL:
    raise ValueError("MORNINGSTAR_API_BASE_URL must be set in the environment variables.")

MORNINGSTAR_API_KEYS: list[str] = [
    key.strip() for key in os.getenv("MORNINGSTAR_API_KEYS").split(",") if key.strip()
]

if not MORNINGSTAR_API_KEYS:
    raise ValueError("MORNINGSTAR_API_KEYS must be set in the environment variables.")

# ─── Rate Limiter Settings ────────────────────────────────────────────────────

# Max requests per second
RATE_LIMIT_PER_SECOND: int = 5

# Max requests per day
RATE_LIMIT_PER_DAY: int = 500

# ─── Cache Settings ───────────────────────────────────────────────────────────

REDIS_URL: str = "redis://localhost:6379/0"

# ─── Alerting ─────────────────────────────────────────────────────────────────

ALERT_EMAIL: str = os.getenv("ALERT_EMAIL")

# ─── Logging & Misc ───────────────────────────────────────────────────────────

LOG_LEVEL: str = "INFO"   # e.g. DEBUG, INFO, WARNING
