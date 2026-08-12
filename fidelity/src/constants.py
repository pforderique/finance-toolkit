"""Constants for the Fidelity portfolio sync tool.

Everything that used to live here as hardcoded sheet/CSV/account config
(PORTFOLIO_TAB, SHEET_RANGE_*, ACCOUNT_NAME_TO_SHEET_LABEL, OMIT_SYMBOL_*,
CSV_*/SHEET_*_COL, REQUIRED_CSV_COLUMNS) now lives in `fidelity/settings.toml`
(see `src/settings.py`) or is resolved live from the sheet by name
(`src/io_layer.resolve_table`). Only genuinely-constant things stay here:
app identity, filesystem layout, and env var names.
"""

from pathlib import Path

# Application metadata
APP_NAME = "fidelity-portfolio-sync"

# Environment variable keys
ENV_SERVICE_ACCOUNT_JSON = "GOOGLE_SERVICE_ACCOUNT_JSON"
ENV_CREDENTIALS_PATH = "GOOGLE_APPLICATION_CREDENTIALS"

# Local filesystem layout
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
ARTIFACTS_DIR = ROOT_DIR / "out"

# Logging
DEFAULT_LOG_LEVEL = "INFO"
