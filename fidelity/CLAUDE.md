# CLAUDE.md — fidelity

Internals and invariants for anyone (human or agent) changing this tool. The
README is the user-facing surface; keep implementation detail here.

## Hard invariants

- **Deterministic CLI. No LLM calls, ever.**
- **Never remove a dry-run path.** `diff` and `sync --dry-run` must always run
  the identical code path as a real write, minus the write.
- **Never undo a sheet write** as "cleanup". Writes are intentional.
- The tool must add/edit/remove rows in `INVESTMENT_HOLDINGS` without
  disturbing anything else in the tab, and nothing at all outside the tab.

## The sheet model (why the write surface is so narrow)

The target tab holds **three native Sheets tables side by side**, anchored to
the same header row and sharing the same physical rows:

| Table | Columns |
|---|---|
| `INVESTMENT_HOLDINGS` | A:G |
| `_PRICE_CACHE` | I:J |
| `MANUAL_BALANCES` | L:M |

Consequences:

- **No `insertDimension`, `deleteDimension`, or `sortRange` against this tab —
  ever.** Any of them scrambles all three tables at once. Row removal is
  implemented as **compaction**: surviving rows rewritten upward inside the
  fixed ranges.
- **Only A:C and G are written.** D (`Mkt_Price`) holds a single
  `ARRAYFORMULA` in the first data row that spills the whole column, keyed on
  Ticker via `XLOOKUP` into `_PRICE_CACHE`; writing into it below the anchor
  breaks the spill and `#REF!`s every row underneath. E (`Total_Equity`) and F
  (`Pct_Gain`) are per-row formulas (`=IF(NOT(B{r}*D{r}=0), B{r}*D{r}, "")`,
  `=IF(C{r}=0, "", (D{r}/C{r})-1)`) pre-filled to the table's last row; they
  only read B/C/D of their own row, so leaving D alone keeps them correct.
- The write is exactly two ranges — `<tab>!A<first>:C<last>` and
  `<tab>!G<first>:G<last>` — computed from the **live-resolved** table range,
  never hardcoded. One `values.batchUpdate`, `USER_ENTERED`, always.

### Capacity ceiling

The table has a fixed row count (219 data slots as of writing). Both the write
guard and the dry-run warning key off the live-resolved range. Raising it is a
manual UI action (drag the table's bottom edge); deliberately not automated,
since it's a structural change to a tab where structural changes are banned.

## Write-path guards

All fatal; the write only happens if every one passes.

1. **Capacity** — plan must fit the table's current row count.
2. **Label validity** — every `Account` value being written must exist in the
   live dropdown (`_Helper!B7:B`, sourced from `=_HELPER[Asset_Holdings]`).
3. **Optimistic concurrency** — re-read the table immediately before writing,
   compare against the snapshot the plan came from, abort on any drift.
4. **Mass-delete threshold** — deletes over 25% of owned rows require
   `--allow-mass-delete`. Guards against a partial/garbled CSV export.

## Artifacts

Every run (dry or real) writes to `fidelity/out/` (gitignored):

- `<ts>_changes.json` — full edit log: spreadsheet/tab/table ids, CSV path +
  sha256, `dry_run`/`applied`, counts, every add/update/delete with prior and
  new values, warnings.
- `<ts>_before.json` — raw read of the entire A:G block captured immediately
  before the write. **This is the rollback artifact**: replay its `values`
  back into the same A:C and G ranges via `values.batchUpdate` to restore.

No flag suppresses these — they're the audit trail.

## Local state (`fidelity/data/`, gitignored)

- `sync_state.json` — written by `workflow.run_apply` only after a successful
  sheet write. Never on a dry run. Backs `fidelity status`.
- `scheduled_last_run.json` — cadence clock for `scheduled-sync`. Separate
  file because a no-op run still counts as "the cadence fired" even though it
  wrote nothing.

## `scheduled-sync` (personal, not a documented feature)

Unattended entry point driven by a personal macOS LaunchAgent
(`com.pfo.fidelity_sync.plist`, gitignored — paths and schedule are
machine-specific). Not in the README on purpose: it's one user's setup, not
part of the tool's public surface.

launchd can't express "every two weeks", so the agent fires every Friday and
`[schedule].min_days_between_runs` (13) drops the off-weeks in-app. The clock
advances only on a run that applied or confirmed a no-op, so a held or errored
Friday retries the next week instead of burning the slot.

Gates, in order — each one below the first emails and writes nothing:

1. Under `min_days_between_runs` since the last run → **silent** skip
2. No CSV matching `csv_glob` in `watch_dir` → email
3. Newest CSV older than `max_age_hours` → email
4. Dry run raises (auth, sheet shape, parse) → email, exit 1
5. `abs(net equity delta)` > `max_net_equity_delta` → email
6. Plan is a no-op → log only, **no email** (keeps the inbox quiet)
7. A write-path guard trips → email

`--allow-mass-delete` is always false on this path. Email reuses
`ms_screener/src/notify.py::send_plain_alert` (`EMAIL_USERNAME`,
`EMAIL_PASSWORD`, `ALERT_EMAILS`); with those unset the job still refuses to
write and just prints the alert to stderr.

`find_latest_csv` swallows `OSError` and returns `None` because macOS TCC
denies `~/Downloads` to processes without Full Disk Access — that should
become an email, not a stack trace.

## Cash is not synced

Fidelity reports `SPAXX`/`FDRXX`/`Pending activity` with a dollar value but no
share count and no cost basis, so they can't be a row in a table whose
`Total_Equity` is shares × price. They're dropped via
`[symbols].ignore_prefixes` / `ignore_exact`. The prefix filter runs **before**
account resolution, so a disabled cash-only account never produces an
"unmapped account" warning. Tracking that cash would mean writing to
`MANUAL_BALANCES` — a deliberately separate, unstarted change. Users don't
need to know any of this, which is why it's here and not in the README.

## Config

Everything lives in `fidelity/settings.toml` (gitignored — real spreadsheet id
and account numbers), with `settings.example.toml` tracked as the template.
`[auth].service_account_json` is the primary credential source;
`GOOGLE_SERVICE_ACCOUNT_JSON` / `GOOGLE_APPLICATION_CREDENTIALS` remain
fallbacks. `_load_settings_or_exit` in `main.py` is the single chokepoint that
wires settings into `io_layer.set_credentials_path`.

Tests fall back to `settings.example.toml` when `settings.toml` is absent, so
a fresh clone passes.

## Git

Real account numbers, the spreadsheet id, and the employer 401(k) name were
purged from history with `git filter-repo` and force-pushed. Don't reintroduce
them into tracked files.
