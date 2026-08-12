# Fidelity Portfolio Sync

Syncs a Fidelity "Positions" CSV export into the **Portfolio** tab of the
Personal_Finance_Tracker Google Sheet, which stores holdings in a native
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
record of what *would* have happened even if nothing was written.

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
   rows require `--yes` or `--allow-mass-delete`. This exists specifically
   so a partial/garbled CSV export can't silently wipe out an account.

## Artifacts and rollback

Every write run (and every dry run, unless `--no-artifacts`) writes into
`fidelity/out/`:

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

## Installation

```bash
cd finance-toolkit
uv sync
```

## Configuration

### Credentials (`.env`)

The root `finance-toolkit/.env` (or `fidelity/.env`) must set:

```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/absolute/path/to/service-account.json
```

(`GOOGLE_APPLICATION_CREDENTIALS` also works as a fallback.) Share the
Google Sheet with that service account's email, with Editor access.

### Sheet + account mapping (`fidelity/settings.toml`)

Everything else — which spreadsheet/tab/table to target, symbol
alias/ignore rules, diff tolerances, and the Fidelity-account-number →
sheet-label mapping — lives in `fidelity/settings.toml`, not in code or env
vars:

```toml
[sheet]
spreadsheet_id = "1Sr8..."
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

`number` is matched first, then case-insensitive `name` (Fidelity renames
accounts occasionally). Set `enabled = false` to keep an account's rows out
of scope entirely (out-of-scope holdings are reported as "untouched", never
touched by adds/updates/deletes). Use `--settings PATH` on any command to
point at a different file.

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

# skip writing out/ artifacts
uv run python -m fidelity.main sync positions.csv --no-artifacts
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

All commands accept `--log-level` (DEBUG/INFO/WARNING/ERROR) and
`--settings PATH`.

## Getting the Fidelity CSV

1. Log in at https://digital.fidelity.com/.
2. **Accounts & Trade → Portfolio**.
3. Choose the account group, then **Download → Positions**.
4. Save as UTF-8 CSV. Header casing/spacing varies between exports
   (`Account Name` vs `Account name`, etc.) — the parser normalizes headers
   and is tolerant of this, but if a future export renames a column outright
   the tool will fail loudly and list the headers it found.

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
