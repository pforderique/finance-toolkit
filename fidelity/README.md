# Fidelity Portfolio Sync

Syncs a Fidelity "Positions" CSV export into the **Portfolio** tab of a
personal finance tracker Google Sheet, which stores holdings in a native
Google Sheets **table** (`INVESTMENT_HOLDINGS`) rather than a plain grid
range. `sync` writes by default; use `diff` (or `sync --dry-run`) to preview
without writing.

## The sheet model

The `Portfolio` tab (gid `417815611`) holds **three native tables side by
side**, all anchored to the same header row and sharing the same physical
rows:

| Table | Columns |
|---|---|
| `INVESTMENT_HOLDINGS` | A:G |
| `_PRICE_CACHE` | I:J |
| `MANUAL_BALANCES` | L:M |

`INVESTMENT_HOLDINGS` columns:

| Col | Name | Type | Written by this tool? |
|---|---|---|---|
| A | Ticker | text | yes |
| B | Shares | DOUBLE | yes |
| C | Avg_Cost | CURRENCY | yes |
| D | Mkt_Price | CURRENCY | **never** |
| E | Total_Equity | CURRENCY | **never** |
| F | Pct_Gain | PERCENT | **never** |
| G | Account | DROPDOWN | yes |

### Why only A:C and G are ever written

- **D (Mkt_Price)** holds a single `ARRAYFORMULA` in D7 that spills down the
  whole column, keyed on the Ticker column via `XLOOKUP` into `_PRICE_CACHE`.
  Writing anything into D8:D225 breaks the spill and replaces live prices
  with `#REF!` for every row below it.
- **E (Total_Equity) and F (Pct_Gain)** are per-row formulas
  (`=IF(NOT(B{r}*D{r}=0), B{r}*D{r}, "")` and `=IF(C{r}=0, "", (D{r}/C{r})-1)`)
  already pre-filled through the table's last row. They only ever need to
  read B/C/D of their own row, so as long as we never touch D, they just work.
- **`_PRICE_CACHE` and `MANUAL_BALANCES` occupy the exact same rows as
  `INVESTMENT_HOLDINGS`.** Any row insert, row delete, or sort issued against
  this tab would scramble all three tables at once. That means the tool
  never issues `insertDimension`, `deleteDimension`, or `sortRange` against
  this tab — full stop. Row removal is implemented as **compaction**
  (rewriting the surviving rows upward within the fixed A:C/G ranges), not
  as a structural delete.

The entire write surface is therefore exactly two ranges, computed from the
live-resolved table range (never hardcoded): `Portfolio!A<first>:C<last>`
and `Portfolio!G<first>:G<last>`. One `values.batchUpdate` call, always.

### 219-row capacity ceiling

`INVESTMENT_HOLDINGS` currently spans rows 7–225 (header row 6) — **219 data
slots**. That's a hard ceiling: `fidelity sync` refuses to write (and
`fidelity diff` / `fidelity sync --dry-run` warns) if the plan needs
more rows than the table currently has.

To raise it: open the sheet, select the `INVESTMENT_HOLDINGS` table, and drag
its bottom edge down in the UI (Sheets tables resize like any other table —
there's no API call for this in the tool, deliberately, since it's a
structural change). Re-run afterward; the tool re-resolves capacity live
every time via `fidelity sheet info` / at the start of every `sync`/`diff`.

## Writes by default

`fidelity sync <csv>` **writes by default**. `fidelity diff <csv>` **never
writes** — it's a pure dry run. `fidelity sync --dry-run` (`-n`) runs the
exact same code path as `diff` without writing, so what a dry run shows is
guaranteed to be what a write would do.

Every dry run (including plain `diff`) also writes an
`out/<ts>_changes.json` artifact with `applied: false`, so you have a
record of what *would* have happened even if nothing was written. This
always happens -- there's no flag to suppress it, since it's the tool's
audit trail.

## Safety guards on the write path

All four are fatal — the write is only attempted if every guard passes:

1. **Capacity** — plan must fit in the table's current row count (see above).
2. **Label validity** — every `Account` value about to be written must exist
   in the live dropdown (`_Helper!B7:B`, sourced from
   `=_HELPER[Asset_Holdings]`). A typo'd or renamed label aborts before
   writing anything.
3. **Optimistic concurrency** — the tool re-reads the table immediately
   before writing and compares it against the snapshot the plan was computed
   from. If anything changed (a human editing the sheet, a concurrent run),
   it aborts and tells you to re-run.
4. **Mass-delete threshold** — deletes exceeding 25% of your currently-owned
   rows require `--allow-mass-delete`. This exists specifically so a
   partial/garbled CSV export can't silently wipe out an account.

## Artifacts and rollback

Every write run (and every dry run) writes into `fidelity/out/`:

- **`<ts>_changes.json`** — the full edit log: spreadsheet id, tab, table
  id/range, CSV path + sha256, `dry_run`/`applied` flags, counts, every
  add/update/delete entry (ticker, account, row, prior/new shares & avg
  cost), and warnings. Written on dry runs too (`applied: false`), so you
  always have a record of what a run computed, whether or not it wrote.
- **`<ts>_before.json`** — a fresh raw read of the *entire* `A:G` block,
  captured immediately before the write. **This is the rollback artifact.**
  To roll back, replay its `values` back into the same `A:C` and `G` ranges
  with a `values.batchUpdate` — restores the prior state exactly, since it's
  the same value-only write mechanism the tool itself uses.

`fidelity/out/` accumulates one pair of files per run; it's covered by
`.gitignore` (`**/out/`) and safe to prune manually at any time.

## Last-synced state

Every real apply (never a dry run or `diff`) also updates
`fidelity/data/sync_state.json` -- a small local record (not written to the
sheet) of the timestamp, the CSV file that was used, the counts, and the net
equity delta of the last successful sync. `fidelity/data/` is gitignored.
Run `fidelity status` to see it:

```bash
uv run python -m fidelity.main status
```

Prints "Never synced" gracefully if the tool hasn't applied a sync yet.

## Prerequisites

Before the tool can do anything, you need all four of these:

1. **Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)** — `brew install uv`.
2. **A Fidelity positions CSV.** Log in to Fidelity → **Portfolio** →
   **Positions** → the **Download** link above the table. It lands in
   `~/Downloads` as `Portfolio_Positions_<Mon-DD-YYYY>.csv`. There is no API
   and no automated download — this step is always manual.
3. **A Google Sheet laid out as described in [The sheet model](#the-sheet-model)
   above.** This is the big one, and the tool cannot create it for you. Your
   sheet needs:
   - a tab (default name `Portfolio`) containing a **native Google Sheets
     table** (Insert → Tables) named `INVESTMENT_HOLDINGS`, whose columns are
     exactly `Ticker`, `Shares`, `Avg_Cost`, `Mkt_Price`, `Total_Equity`,
     `Pct_Gain`, `Account`;
   - `Mkt_Price`, `Total_Equity`, and `Pct_Gain` populated by **your own
     formulas** — the tool never writes them (see
     [Why only A:C and G are ever written](#why-only-ac-and-g-are-ever-written));
   - the `Account` column set up as a dropdown, with one option per account
     label you plan to use (e.g. `Fidelity Brokerage`, `Fidelity Roth IRA`).
     Run `fidelity accounts labels` to have the tool read the live list back.
4. **A Google service account with access to that sheet** — see below.

## Installation

```bash
cd finance-toolkit
uv sync
cp fidelity/settings.example.toml fidelity/settings.toml
```

## Configuration

### Google credentials

The tool authenticates as a **service account** — a robot Google account with
its own email address. You create it once, then share your sheet with it, the
same way you'd share with a coworker.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a project (any name).
2. **APIs & Services → Library** → search "Google Sheets API" → **Enable**.
3. **APIs & Services → Credentials → Create Credentials → Service account**.
   Give it a name; skip the optional role/access steps; click **Done**.
4. Click the new service account → **Keys** → **Add Key → Create new key →
   JSON**. A `.json` file downloads. Keep it — it's a password; don't commit it.
5. Copy the service account's email (it looks like
   `something@your-project.iam.gserviceaccount.com` — it's on the same page).
6. Open your Google Sheet → **Share** → paste that email → give it **Editor** →
   send.

Then point the tool at the key file. In `finance-toolkit/.env` (or
`fidelity/.env`):

```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/absolute/path/to/service-account.json
```

(`GOOGLE_APPLICATION_CREDENTIALS` works as a fallback.)

Verify it all worked:

```bash
uv run python -m fidelity.main sheet info
```

That's a read-only call. If it prints your table's gid, range, and capacity,
you're set. A `403` means step 6 didn't take; a `404` means `spreadsheet_id` is
wrong.

### Sheet + account mapping (`fidelity/settings.toml`)

Everything else — which spreadsheet/tab/table to target, symbol
alias/ignore rules, diff tolerances, the scheduled-run gates, and the
Fidelity-account-number → sheet-label mapping — lives in
`fidelity/settings.toml`, not in code or env vars.

**`settings.toml` is gitignored**, because it contains your real spreadsheet id
and your real Fidelity account numbers. The tracked file is
`fidelity/settings.example.toml`; copy it and edit your copy.

```toml
[sheet]
# the long id from your sheet's URL: .../spreadsheets/d/<THIS>/edit
spreadsheet_id = "YOUR_SPREADSHEET_ID_HERE"
tab            = "Portfolio"
table          = "INVESTMENT_HOLDINGS"

[symbols]
ignore_prefixes = ["SPAXX", "FDRXX"]
ignore_exact    = ["PENDING ACTIVITY"]
aliases         = { BRKB = "BRK.B" }

[tolerance]
shares   = 1e-6
avg_cost = 0.005

[[accounts]]
number  = "X00000000"
name    = "Individual"
label   = "Fidelity Brokerage"
enabled = true
```

`number` and `name` are the first two columns of your positions CSV; `label`
must exactly match one of your sheet's Account dropdown options. `number` is
matched first, then case-insensitive `name` (Fidelity renames accounts
occasionally). Set `enabled = false` to keep an account's rows out of scope
entirely (out-of-scope holdings are reported as "untouched", never touched by
adds/updates/deletes). Use `--settings PATH` on any command to point at a
different file.

You can manage these blocks from the CLI instead of editing TOML by hand — see
`fidelity accounts add|edit|remove` below.

### Cash is not synced

Fidelity reports core/money-market positions (`SPAXX`, `FDRXX`) and `Pending
activity` with a dollar value but **no share count and no cost basis**, so they
can't be expressed as a row in a holdings table — `Total_Equity` is computed
from shares × price. They're skipped via `[symbols].ignore_prefixes`. If you
want that cash counted in your net worth, track it separately (the reference
sheet uses a `MANUAL_BALANCES` table for exactly this).

## Commands

### `fidelity diff <csv>`
Show the plan against the live sheet. Read-only; never writes.
```bash
uv run python -m fidelity.main diff ~/Downloads/Portfolio_Positions_Aug-10-2026.csv
```

### `fidelity sync <csv> [--dry-run]`
Same plan as `diff`. Writes by default: runs all four guards and, if they
pass, writes. With `--dry-run` (`-n`), identical to `diff` — no write.
```bash
# actually write
uv run python -m fidelity.main sync positions.csv

# preview only, no write
uv run python -m fidelity.main sync positions.csv --dry-run

# force through the mass-delete guard
uv run python -m fidelity.main sync positions.csv --allow-mass-delete

# preserve row positions instead of compacting survivors upward
uv run python -m fidelity.main sync positions.csv --no-compact
```

### `fidelity status`
Show the last successfully-applied sync: timestamp (+ relative age), the CSV
file used, and its counts/net equity delta. Reads local state only — never
touches the sheet. Prints "Never synced" if `sync` has never been applied.
```bash
uv run python -m fidelity.main status
```

### `fidelity scheduled-sync`
The unattended entry point used by the LaunchAgent (see
[Scheduled runs](#scheduled-runs)). Finds the newest CSV in
`[schedule].watch_dir`, dry runs it, and writes **only** if every gate passes.
Any gate that trips sends an email and leaves the sheet untouched.
```bash
uv run python -m fidelity.main scheduled-sync

# bypass only the net-equity-delta tripwire (all other guards stay armed)
uv run python -m fidelity.main scheduled-sync --force
```

### `fidelity sheet info`
Print the live-resolved gid, tableId, range, columns, and capacity/used
rows. Useful for confirming the tool is pointed at the right table before
running a sync.
```bash
uv run python -m fidelity.main sheet info
```

### `fidelity accounts list`
List every configured account mapping.
```bash
uv run python -m fidelity.main accounts list
```

### `fidelity accounts labels`
Live-read the valid `Account` dropdown values from the sheet
(`_Helper[Asset_Holdings]`) — also proves auth is working.
```bash
uv run python -m fidelity.main accounts labels
```

### `fidelity accounts add`
```bash
uv run python -m fidelity.main accounts add \
  --number 000000001 --name "ROTH IRA" --label "Fidelity Roth IRA"

# skip validating --label against the live dropdown
uv run python -m fidelity.main accounts add \
  --number 999 --name "New Acct" --label "Some New Label" --force

# add as disabled (mapped but out of scope until enabled)
uv run python -m fidelity.main accounts add \
  --number 000000003 --name "Traditional IRA" --label "Fidelity Brokerage" --disabled
```

### `fidelity accounts edit`
```bash
uv run python -m fidelity.main accounts edit 000000001 --label "Fidelity Roth IRA"
uv run python -m fidelity.main accounts edit 000000003 --enable
uv run python -m fidelity.main accounts edit 000000003 --disable
```

### `fidelity accounts remove`
```bash
uv run python -m fidelity.main accounts remove 999
uv run python -m fidelity.main accounts remove 999 --yes   # skip confirmation
```

All commands accept `--settings PATH` to point at a different
`settings.toml` (defaults to `fidelity/settings.toml`).

## Getting the Fidelity CSV

1. Log in at https://digital.fidelity.com/.
2. **Accounts & Trade → Portfolio**.
3. Choose the account group, then **Download → Positions**.
4. Save as UTF-8 CSV. Header casing/spacing varies between exports
   (`Account Name` vs `Account name`, etc.) — the parser normalizes headers
   and is tolerant of this, but if a future export renames a column outright
   the tool will fail loudly and list the headers it found.

## Scheduled runs

`fidelity scheduled-sync` is the unattended path: a LaunchAgent runs it at
4:30pm ET on weekdays (after market close), it picks up whatever positions CSV
you last downloaded, and it syncs — but only if nothing looks wrong.

Because nobody is watching, it is deliberately more paranoid than `sync`. It
**dry runs first, every time**, and applies only if all of these hold:

| Gate | If it trips |
|---|---|
| A CSV matching `[schedule].csv_glob` exists in `watch_dir` | email, no write |
| That CSV is newer than `max_age_hours` (default 36h) | email, no write |
| The dry run succeeds (auth, sheet shape, parse) | email, no write, exit 1 |
| `abs(net equity delta)` ≤ `max_net_equity_delta` (default $10,000) | email, no write |
| All four [write-path guards](#safety-guards-on-the-write-path) pass | email, no write |

The delta tripwire is the important one. A big jump usually means the sheet
drifted for weeks (the sync is legitimate but worth eyeballing) or the CSV is
partial/garbled (the sync is wrong). Either way the right move is a human
glance, not a silent write, so the job emails you the plan and skips. Nothing
is queued — the next run just tries again, and if you sync manually in the
meantime the delta is gone.

A plan with no adds/updates/deletes is a silent no-op: **no email**, so an
idempotent daily job doesn't fill your inbox.

Mass deletes are never auto-approved here — `--allow-mass-delete` is always
false on this path, so a garbled export becomes an email, not a wipe.

### Configuration

```toml
[schedule]
watch_dir            = "~/Downloads"
csv_glob             = "Portfolio_Positions_*.csv"
max_age_hours        = 36.0
max_net_equity_delta = 10000.0
```

The newest match **by mtime** wins (a re-download of an older-dated file is
still fresh data).

Email reuses the toolkit's existing SMTP channel — no new service. In `.env`:

```bash
EMAIL_USERNAME=you@gmail.com
EMAIL_PASSWORD=your-app-password   # Gmail app password, not your login
ALERT_EMAILS=you@gmail.com         # optional; defaults to EMAIL_USERNAME
```

If those aren't set, the job still runs and still refuses to write when a gate
trips — it just prints the alert to stderr instead of emailing it.

### Installing the LaunchAgent

```bash
cp fidelity/com.pfo.fidelity_sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pfo.fidelity_sync.plist
```

Edit the plist first if your checkout isn't at
`/Users/pfo/ws/finance/finance-toolkit` or your `uv` isn't at
`/Users/pfo/.local/bin/uv` (`which uv`). Logs go to
`~/Library/Logs/fidelity_sync.log` and `.err`.

Test it without waiting for 4:30pm:

```bash
launchctl start com.pfo.fidelity_sync
tail -20 ~/Library/Logs/fidelity_sync.log
```

To disable: `launchctl unload ~/Library/LaunchAgents/com.pfo.fidelity_sync.plist`.

Note the schedule is in **local machine time** — the 16:30 entry assumes the
machine is on US Eastern. Adjust the hour if you're elsewhere. If the Mac is
asleep at the scheduled time, launchd runs the job when it next wakes; the
staleness gate is what keeps a delayed run from applying an old CSV.

## Troubleshooting

- **Missing credentials**: confirm `GOOGLE_SERVICE_ACCOUNT_JSON` (or
  `GOOGLE_APPLICATION_CREDENTIALS`) points at a readable JSON key file.
- **Sheets access error**: confirm `settings.toml`'s `spreadsheet_id` is
  correct and the service account has Editor access.
- **"Table has room for N rows, plan needs M"**: capacity guard — see
  "219-row capacity ceiling" above.
- **"label(s) not present in the live Account dropdown"**: an
  `accounts.toml` label doesn't match `_Helper[Asset_Holdings]` anymore —
  fix the label in `settings.toml` (or the sheet's dropdown source) and
  re-run.
- **"sheet changed since this plan was computed"**: someone (or another
  run) edited the sheet between the plan and the write. Just re-run.
- **CSV parsing warnings**: printed in yellow; unmapped accounts, unparsable
  rows (protected from deletion, never silently dropped), and duplicate-row
  aggregation are all reported this way.
