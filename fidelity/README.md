# Fidelity Portfolio Sync Tool

Keep your personal Google Sheet portfolio tracker in sync with the latest Fidelity holdings export. This Typer-based CLI ingests the CSV that Fidelity provides, normalizes the positions, and updates the tracker tab in Google Sheets while writing local change logs and snapshots for auditing.

## Requirements
- Python 3.10 or later
- A Google Cloud project with the Google Sheets API enabled
- Access to the Google Sheets workbook you want to update
- A Fidelity brokerage account with access to the Positions CSV download

---

## Installation
1. **Clone and enter the repo**
   ```bash
   git clone https://github.com/pforderique/finance-toolkit.git
   ```
2. **Create and activate a virtual environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. **Install dependencies** (installs the toolkit in editable mode)
   ```bash
   pip install -r requirements.txt
   ```
4. **Navigate to the fidelity module**
   ```bash
   cd finance-toolkit/fidelity
   ```
---

## Google Cloud Setup

### 0. Create a Google Cloud project
- Console guide: https://cloud.google.com/resource-manager/docs/creating-managing-projects

### 1. Enable the Sheets API
- Console UI: https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com

### 2. Create a service account
- Console guide: https://cloud.google.com/iam/docs/service-accounts-create

### 3. Generate a JSON key and store it securely
- Console guide: https://cloud.google.com/iam/docs/creating-managing-service-account-keys
- Download the JSON key file and save it to a secure location on your local machine (e.g., `~/secrets/fidelity-sync-sa.json`).

### 4. Share the destination Google Sheet
1. Open the portfolio tracker Google Sheet.
2. Use **Share** → add *fidelity-sync@\<PROJECT_ID\>.iam.gserviceaccount.com* with **Editor** access.
3. Copy the spreadsheet ID from the URL (`https://docs.google.com/spreadsheets/d/<SHEET_ID>/...`).

### 5. Configure environment variables
Add the following to `fidelity/.env` (or export them in your shell):
```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/absolute/path/to/fidelity-sync-sa.json
SHEET_ID=<your_google_sheet_id>
```

---

## Getting the Fidelity CSV
1. Log in at https://digital.fidelity.com/.
2. Navigate to **Accounts & Trade → Portfolio**.
3. Choose the relevant account group and click **Download** → **Positions**.
4. Save the CSV (UTF-8) locally; do not change the headers or column order.

---

## Running the CLI
Activate your virtual environment and run:
```bash
python main.py ~/Downloads/Fidelity_Positions.csv
```
Key options:
- `--sheet-id` – overrides the `SHEET_ID` environment variable.
- `--tab-name` – defaults to `Portfolio_Tracker` (see `fidelity/src/constants.py` for all defaults).
- `--dry-run` – process the CSV and show the diff without writing to Google Sheets.
- `--log-level` – defaults to `INFO`; use `DEBUG` for verbose tracing.

View command help at any time:
```bash
python main.py --help
```

---

## What the Workflow Produces
- **Google Sheets updates** – Portfolio rows matching Fidelity holdings are updated or created; rows with no corresponding holding are removed.
- **Snapshots** – `fidelity/out/*_previous_portfolio.csv` and `*_updated_portfolio.csv` capture the pre- and post-update sheet state.
- **Change log** – `fidelity/out/*_edits.json` summarizes adds, updates, and removals.
- **Logs** – Structured console output plus optional files under `fidelity/logs/` (see `fidelity/src/logging_setup.py`).

---

## Troubleshooting
- *Missing credentials error*: confirm `GOOGLE_SERVICE_ACCOUNT_JSON` (or `GOOGLE_APPLICATION_CREDENTIALS`) points to a readable JSON key file.
- *Sheets access error*: ensure the spreadsheet ID is correct and the service account email has editor access.
- *CSV validation warnings*: the tool skips rows with unrecognized accounts, omitted tickers, or malformed numbers; warnings are printed in yellow.
- *Formula reuse*: new rows automatically include standard GOOGLEFINANCE formulas (see `fidelity/src/workflow.py`).

---

## Next Steps
- Automate CSV downloads and CLI execution with a cron job or GitHub Action using the same environment variables.
- Store service account secrets in a password manager or secret manager instead of plain files for long-term use.

