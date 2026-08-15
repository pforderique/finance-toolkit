# Fidelity Portfolio Sync

Syncs a Fidelity "Positions" CSV export into a holdings table in your Google
Sheet. `diff` previews, `sync` writes.

```bash
uv run python -m fidelity.main diff ~/Downloads/Portfolio_Positions_Aug-10-2026.csv   # preview
uv run python -m fidelity.main sync ~/Downloads/Portfolio_Positions_Aug-10-2026.csv   # write
```

## Prerequisites

1. **[`uv`](https://docs.astral.sh/uv/)** — `brew install uv`.
2. **A Google Sheet** with a tab named `Portfolio` containing a native Google
   Sheets table (Insert → Tables) named `INVESTMENT_HOLDINGS`, in **columns
   A:G**. The header row can start anywhere; the tool finds it. Both names are
   configurable in `settings.toml`.

   The seven columns, in order:

   | Col | Name | Written by the tool |
   |---|---|---|
   | A | `Ticker` | yes |
   | B | `Shares` | yes |
   | C | `Avg_Cost` | yes |
   | D | `Mkt_Price` | no — your formula |
   | E | `Total_Equity` | no — your formula |
   | F | `Pct_Gain` | no — your formula |
   | G | `Account` | yes |

   `Account` should be a dropdown listing your account names (e.g.
   `Fidelity Brokerage`, `Fidelity Roth IRA`). The tool only ever touches
   A, B, C, and G — your price and gain formulas, and anything else in the
   sheet, are left alone.
3. **A Google service account** with Editor access to that sheet — see below.
4. **A Fidelity positions CSV.** Fidelity → **Accounts & Trade → Portfolio →
   Positions → Download**. Lands in `~/Downloads`. This step is always manual;
   there's no API.

## Setup

```bash
cd finance-toolkit
uv sync
cp fidelity/settings.example.toml fidelity/settings.toml
```

Then edit `fidelity/settings.toml` — it's commented throughout and it's the
only file you need to configure. It's gitignored, since it holds your real
spreadsheet id and account numbers.

### Google access

The tool signs in as a **service account**: a robot Google account with its own
email, which you share your sheet with like you would a coworker.

1. At [console.cloud.google.com](https://console.cloud.google.com), create a
   project (any name).
2. **APIs & Services → Library** → "Google Sheets API" → **Enable**.
3. **APIs & Services → Credentials → Create Credentials → Service account**.
   Name it, skip the optional steps, **Done**.
4. Click it → **Keys → Add Key → Create new key → JSON**. A file downloads —
   treat it like a password.
5. Copy the service account's email (`…@….iam.gserviceaccount.com`).
6. In your Google Sheet: **Share** → paste that email → **Editor** → send.

Put the key's path in `settings.toml` under `[auth].service_account_json`, then
check everything works:

```bash
uv run python -m fidelity.main sheet info
```

Read-only. If it prints your table's range and capacity, you're done. `403`
means step 6 didn't take; `404` means `spreadsheet_id` is wrong.

## Commands

| Command | What it does |
|---|---|
| `diff <csv>` | Show what would change. Never writes. |
| `sync <csv>` | Write the changes. `--dry-run` / `-n` makes it identical to `diff`. |
| `status` | When the last sync ran, from what CSV, and what it changed. |
| `sheet info` | The live table's range, columns, and remaining capacity. |
| `accounts list` | Your configured account mappings. |
| `accounts labels` | The Account dropdown values read live from the sheet. |
| `accounts add\|edit\|remove` | Manage account mappings without editing TOML. |

All take `--settings PATH` to use a different config file.

Run any of them as `uv run python -m fidelity.main <command>`.

### Safety

`sync` refuses to write if the plan doesn't fit the table, if an account label
isn't in the sheet's dropdown, if the sheet changed since the preview was
computed, or if it would delete more than 10% of your rows (override with
`--allow-mass-delete`). Every run — dry or real — logs what it did to
`fidelity/out/`, including a snapshot you can replay to roll back.

## Troubleshooting

- **"Service account key not found"** — `[auth].service_account_json` in
  `settings.toml` isn't pointing at a readable JSON file.
- **`403` / "Sheets access error"** — the service account isn't shared on the
  sheet as Editor.
- **"Table has room for N rows, plan needs M"** — select the table in Sheets
  and drag its bottom edge down, then re-run.
- **"label(s) not present in the live Account dropdown"** — a `label` in
  `settings.toml` doesn't match the sheet. Run `accounts labels` to see the
  real values.
- **"sheet changed since this plan was computed"** — something edited the
  sheet mid-run. Just re-run.
- **Yellow warnings on a run** — unmapped accounts, unparsable rows (protected
  from deletion, never silently dropped), or duplicate rows being merged.
