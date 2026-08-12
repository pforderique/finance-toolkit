# Finance Toolkit

Personal investment tools: stock screening, portfolio sync, and buy-signal analysis.

## Tools

| Package | What it does |
|---|---|
| `ms_screener` | Fetches Morningstar data into Google Sheets (Selenium + Sheets API) |
| `fidelity` | Syncs a Fidelity CSV export into the Portfolio Tracker sheet |
| `trader_agent` | Buy-signal analyst — scored morning briefs via Claude Code |

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Install all packages + dev deps
uv sync

# Copy and fill in the env file
cp .env.example .env  # or edit .env directly
```

### Environment variables (`.env` at repo root)

| Variable | Used by | Description |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | all | Path to GCP service account JSON |
| `SHEET_ID` | ms_screener, trader_agent | Morningstar Screener Google Sheet ID |
| `MORNINGSTAR_API_KEYS` | ms_screener | Comma-separated RapidAPI keys |
| `EMAIL_USERNAME` / `EMAIL_PASSWORD` | ms_screener | Alert email credentials |
| `ALERT_EMAILS` | ms_screener | Comma-separated recipient addresses |
| `SPL_BARCODE` / `SPL_PIN` | ms_screener | Seattle Public Library card (Morningstar access) |

`fidelity` no longer reads a spreadsheet id from `.env` — its target
spreadsheet/tab/table and account mappings live in
[`fidelity/settings.toml`](./fidelity/settings.toml) instead (`--settings`
to override the path). See [fidelity/README.md](./fidelity/README.md).

## Usage

```bash
# Morning brief (via Claude Code)
# Say "morning brief" or "tell me which stocks to buy" in a Claude Code session
# from this directory — it reads trader_agent/CLAUDE.md and drives the tools.

# Run ms_screener manually
uv run python -m ms_screener.main --help

# Sync Fidelity portfolio
uv run python -m fidelity.main positions.csv

# Run tests
uv run pytest
```

## Scheduled jobs

`ms_screener` runs automatically weekdays at 7:05 AM via macOS LaunchAgent.
See [MS_SCREENER_LAUNCHD.md](./MS_SCREENER_LAUNCHD.md).
