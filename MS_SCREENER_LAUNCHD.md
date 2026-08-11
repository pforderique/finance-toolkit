# ms_screener macOS LaunchAgent Configuration

The `ms_screener` tool runs automatically via macOS LaunchAgent. The plist file is located at:

```
~/Library/LaunchAgents/com.pfo.ms_screener.plist
```

## Current Schedule

Runs **Monday–Friday at 7:05 AM** (weekdays only).

## Failure Alarms

A run that dies used to be visible only in `~/Library/Logs/ms_screener.err`,
which nobody reads — that is how the Jun–Aug 2026 outage stayed silent for five
weeks while the morning brief kept looking normal. Two alarms now cover it:

1. **Immediate email on a failed run.** `ms_screener/main.py` calls
   `ms_screener.src.notify.notify_run_failure()` whenever the workflow raises.
   It emails `ALERT_EMAILS` (falling back to `EMAIL_USERNAME`) over the same
   Gmail SMTP credentials the brief already uses, with the traceback and the
   tail of both log files attached. If the credentials are missing it prints a
   banner to stderr instead — it never masks the original failure.

2. **Red staleness banner in the morning brief.** `trader_agent/tools/health.py`
   checks the Screener tab's `last_scraped`, the run log, today's
   `*_scores.json` snapshot, and the newest `ratings_date` in the universe.
   Any problem renders a banner above the "What Changed" box and prefixes the
   email subject with `🚨 STALE DATA —`. This is the backstop for the failure
   modes that produce no traceback at all: the LaunchAgent being unloaded, the
   Mac being asleep at 7:05, or a run that "succeeds" with a frozen column.

Check the pipeline by hand at any time:

```bash
cd /Users/pfo/ws/finance/finance-toolkit
uv run python -m trader_agent.tools.health          # exit 0 healthy, 1 unhealthy
uv run python -m trader_agent.tools.health --json
uv run python -m trader_agent.tools.health --email  # mail the report if unhealthy
```

`--email` needs `EMAIL_USERNAME`, `EMAIL_PASSWORD` and `ALERT_EMAILS` in `.env`.

## Common Tasks

### View logs
```bash
tail -f ~/Library/Logs/ms_screener.log      # stdout
tail -f ~/Library/Logs/ms_screener.err      # stderr
```

### Check if the job is loaded
```bash
launchctl list | grep ms_screener
```

### Reload after editing the plist
```bash
launchctl unload ~/Library/LaunchAgents/com.pfo.ms_screener.plist
launchctl load ~/Library/LaunchAgents/com.pfo.ms_screener.plist
```

### Disable the job (without removing it)
```bash
launchctl unload ~/Library/LaunchAgents/com.pfo.ms_screener.plist
```

### Re-enable the job
```bash
launchctl load ~/Library/LaunchAgents/com.pfo.ms_screener.plist
```

## Edit the Schedule

To change the schedule, edit `~/Library/LaunchAgents/com.pfo.ms_screener.plist`:

**Current:**
```xml
<key>StartCalendarInterval</key>
<array>
  <!-- Monday through Friday -->
  <dict>
    <key>Hour</key><integer>7</integer>
    <key>Minute</key><integer>5</integer>
    <key>Weekday</key><integer>1</integer>  <!-- 1=Mon, 2=Tue, ..., 5=Fri -->
  </dict>
  <!-- ... repeat for each day -->
</array>
```

**To run daily at 6:00 AM:**
```xml
<key>StartCalendarInterval</key>
<dict>
  <key>Hour</key><integer>6</integer>
  <key>Minute</key><integer>0</integer>
</dict>
```

Then reload:
```bash
launchctl unload ~/Library/LaunchAgents/com.pfo.ms_screener.plist
launchctl load ~/Library/LaunchAgents/com.pfo.ms_screener.plist
```

## Environment Variables

Environment variables are loaded from:
- `~/Library/LaunchAgents/com.pfo.ms_screener.plist` (ProgramArguments)
- `finance-toolkit/ms_screener/.env` (via `dotenv` in main.py)

To update `.env`:
```bash
cd /Users/pfo/ws/finance/finance-toolkit
# Edit ms_screener/.env
nano ms_screener/.env
```

Changes take effect on the next scheduled run (or immediately if you test manually).

## Test Run

To test without waiting for the schedule:
```bash
cd /Users/pfo/ws/finance/finance-toolkit
source ~/envs/finance/bin/activate  # or however you manage Python
python ms_screener/main.py --auto
```
