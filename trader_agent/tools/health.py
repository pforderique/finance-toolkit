"""Pipeline staleness / health alarm for the ms_screener -> morning brief chain.

Why this exists
---------------
In late Jun 2026 the ms_screener LaunchAgent job started failing every single
weekday morning (`Error: Timed out waiting for Morningstar CSV to finish
downloading.`). Nothing downstream noticed: the morning brief kept rendering,
kept ranking, kept emailing — off a Screener tab whose `ratings_date` column had
been frozen for five weeks. The failure was only caught by hand-checking
Morningstar. The bug was fixable; the *silence* was the real defect.

This module is the smoke detector. It answers one question — "is the data behind
today's brief actually fresh?" — from three independent angles, so no single
broken component can hide the failure:

  1. `last_scraped` on the Screener tab   -> did the scraper write anything?
  2. `~/Library/Logs/ms_screener.log`     -> did the last run finish or blow up?
  3. `trader_agent/logs/*_scores.json`    -> did the scorer produce a snapshot?

Plus a supplementary universe-wide `ratings_date` check, which catches the
nastier variant of the same bug: the run "succeeds", `last_scraped` advances,
but every rating stays frozen.

Thresholds and why
------------------
`STALE_MEDIAN_DAYS = 3` — the job runs Mon-Fri. The longest *legitimate* gap is
a Friday scrape being read by a Monday morning brief: exactly 3 calendar days.
So the trigger is strictly `> 3`.

`STALE_BUSINESS_DAYS = 2` — calendar days alone would cry wolf on a long
weekend (Fri scrape + Monday holiday + Tuesday brief = 4 calendar days, but
only 2 business days, and only ONE missed run). Requiring *both* thresholds
means a real outage still trips the alarm after two consecutive missed runs
(Fri scrape read on Wed = 5 calendar / 3 business), while holidays stay quiet.

`RATINGS_MAX_AGE_DAYS = 21` — Morningstar refreshes some part of a ~100-name
US large-cap universe constantly. If the *newest* `ratings_date` anywhere in the
universe is three weeks old, the date pipeline is stuck, whatever the log says.

Usage
-----
    python -m trader_agent.tools.health              # text report, exit 1 if bad
    python -m trader_agent.tools.health --json
    python -m trader_agent.tools.health --no-fetch   # skip the Sheets read
    python -m trader_agent.tools.health --email      # mail the report if unhealthy
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

# --------------------------------------------------------------------------
# tunables
# --------------------------------------------------------------------------

#: Median `last_scraped` age (calendar days) above which data is considered stale.
STALE_MEDIAN_DAYS = 3
#: ...but only if this many business days have also elapsed (holiday guard).
STALE_BUSINESS_DAYS = 2
#: Newest `ratings_date` in the whole universe older than this => frozen pipeline.
RATINGS_MAX_AGE_DAYS = 21
#: How many business days without a completed ms_screener run before we shout.
MAX_BUSINESS_DAYS_WITHOUT_RUN = 2

DEFAULT_LOG_PATH = Path.home() / "Library" / "Logs" / "ms_screener.log"
DEFAULT_LOGS_DIR = Path(__file__).parent.parent / "logs"

SEVERITY_RANK = {"ok": 0, "warn": 1, "critical": 2}

# `│ M* Workflow  •  2026-08-07 07:05:03 │` (rich box header, unicode borders)
_RUN_HEADER_RE = re.compile(
    r"M\*\s*Workflow\s*[•·]\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
)
_ERROR_MARKERS = ("Error:", "Traceback (most recent call last)")
#: Presence of the rich summary table means the workflow reached the end.
_SUCCESS_MARKERS = ("Snapshot rows", "Rows ingested")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def parse_date_loose(val) -> Optional[date]:
    """Parse the several date shapes that leak out of Sheets.

    The Screener tab is genuinely mixed: `last_scraped` comes back as both
    `8/10/2026` and `2026-08-10`, and `ratings_date` as `Aug 7, 2026`.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()
    if not text:
        return None
    # ISO datetimes / anything with a time component
    head = text.split("T")[0].split(" ")[0]
    for candidate in (text, head):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def business_days_between(start: date, end: date) -> int:
    """Count weekdays in the half-open interval (start, end].

    Fri -> Mon == 1, Fri -> Tue == 2. Negative spans count as 0.
    """
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _problem(check: str, severity: str, message: str) -> dict:
    return {"check": check, "severity": severity, "message": message}


# --------------------------------------------------------------------------
# check 1 — Screener `last_scraped` freshness
# --------------------------------------------------------------------------

def check_scrape_freshness(rows: Optional[Iterable[dict]], as_of: date) -> dict:
    """Median age of `last_scraped` across the Screener rows."""
    result = {
        "name": "scrape_freshness",
        "severity": "ok",
        "checked": False,
        "median_age_days": None,
        "business_days": None,
        "max_age_days": None,
        "total_rows": 0,
        "stale_tickers": 0,
        "unparseable": 0,
        "newest_scrape": None,
        "oldest_scrape": None,
        "detail": "",
    }
    if rows is None:
        result["detail"] = "Screener tab not read."
        return result

    rows = list(rows)
    result["checked"] = True
    result["total_rows"] = len(rows)
    if not rows:
        result["severity"] = "critical"
        result["detail"] = "Screener tab is empty."
        return result

    parsed: list[date] = []
    for row in rows:
        d = parse_date_loose(row.get("last_scraped"))
        if d is None:
            result["unparseable"] += 1
        else:
            parsed.append(d)

    if not parsed:
        result["severity"] = "critical"
        result["detail"] = (
            f"No usable last_scraped value on any of {len(rows)} Screener rows."
        )
        return result

    ages = sorted((as_of - d).days for d in parsed)
    median_age = int(statistics.median(ages))
    median_date = as_of - timedelta(days=median_age)

    result["median_age_days"] = median_age
    result["max_age_days"] = ages[-1]
    result["business_days"] = business_days_between(median_date, as_of)
    result["stale_tickers"] = sum(1 for a in ages if a > STALE_MEDIAN_DAYS)
    result["newest_scrape"] = max(parsed).isoformat()
    result["oldest_scrape"] = min(parsed).isoformat()

    is_stale = (
        median_age > STALE_MEDIAN_DAYS
        and result["business_days"] > STALE_BUSINESS_DAYS
    )
    if is_stale:
        result["severity"] = "critical"
        result["detail"] = (
            f"Screener data is {_plural(median_age, 'day')} old "
            f"(median last_scraped {median_date.isoformat()}); "
            f"{result['stale_tickers']} of {len(rows)} tickers stale."
        )
    elif result["unparseable"] and result["unparseable"] >= len(rows) // 2:
        result["severity"] = "warn"
        result["detail"] = (
            f"{result['unparseable']} of {len(rows)} rows have an unreadable "
            "last_scraped value."
        )
    else:
        result["detail"] = (
            f"Median last_scraped {median_date.isoformat()} "
            f"({_plural(median_age, 'day')} old)."
        )
    return result


# --------------------------------------------------------------------------
# check 2 — last ms_screener run
# --------------------------------------------------------------------------

def parse_run_log(text: str) -> list[dict]:
    """Split the ms_screener stdout log into per-run blocks.

    Each run starts at a `M* Workflow  •  <timestamp>` banner and ends where the
    next one begins, so an `Error:` printed just before the next banner belongs
    to the run above it.
    """
    matches = list(_RUN_HEADER_RE.finditer(text))
    runs: list[dict] = []
    for i, m in enumerate(matches):
        stamp = m.group(1).replace("T", " ")
        try:
            started = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]

        error_line = None
        for line in body.splitlines():
            stripped = line.strip()
            if any(stripped.startswith(mark) for mark in _ERROR_MARKERS):
                error_line = stripped
                break

        if error_line:
            status = "error"
        elif any(mark in body for mark in _SUCCESS_MARKERS):
            status = "ok"
        else:
            # No error printed and no summary table => the process died mid-run
            # (killed, machine slept, uncaught crash on stderr only).
            status = "incomplete"

        runs.append({
            "started": started.isoformat(sep=" "),
            "date": started.date().isoformat(),
            "status": status,
            "error": error_line,
        })
    return runs


def check_last_run(log_path: Path = DEFAULT_LOG_PATH, as_of: Optional[date] = None) -> dict:
    """Did the most recent ms_screener run succeed, and was it recent?"""
    as_of = as_of or date.today()
    result = {
        "name": "last_run",
        "severity": "ok",
        "checked": False,
        "log_path": str(log_path),
        "last_run": None,
        "last_run_status": None,
        "last_run_error": None,
        "age_days": None,
        "consecutive_failures": 0,
        "detail": "",
    }

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["severity"] = "warn"
        result["detail"] = f"Cannot read ms_screener log ({exc.__class__.__name__}): {log_path}"
        return result

    runs = parse_run_log(text)
    result["checked"] = True
    if not runs:
        result["severity"] = "warn"
        result["detail"] = f"No ms_screener run headers found in {log_path}."
        return result

    last = runs[-1]
    last_date = date.fromisoformat(last["date"])
    result["last_run"] = last["started"]
    result["last_run_status"] = last["status"]
    result["last_run_error"] = last["error"]
    result["age_days"] = (as_of - last_date).days

    # How many runs back does the failure streak go?
    failures = 0
    for run in reversed(runs):
        if run["status"] == "ok":
            break
        failures += 1
    result["consecutive_failures"] = failures

    missed = business_days_between(last_date, as_of)
    if last["status"] != "ok":
        result["severity"] = "critical"
        reason = last["error"] or f"run ended {last['status']} (no summary written)"
        streak = (
            f" — {_plural(failures, 'consecutive failed run')}"
            if failures > 1 else ""
        )
        result["detail"] = (
            f"Last ms_screener run ({last['started']}) failed{streak}: {reason}"
        )
    elif missed > MAX_BUSINESS_DAYS_WITHOUT_RUN:
        result["severity"] = "critical"
        result["detail"] = (
            f"ms_screener has not run since {last['started']} "
            f"({_plural(missed, 'business day')} ago) — is the LaunchAgent loaded?"
        )
    else:
        result["detail"] = f"Last ms_screener run OK at {last['started']}."
    return result


# --------------------------------------------------------------------------
# check 3 — today's scorer snapshot
# --------------------------------------------------------------------------

def check_snapshot(logs_dir: Path = DEFAULT_LOGS_DIR, as_of: Optional[date] = None) -> dict:
    """Was a `*_scores.json` snapshot written for today?"""
    as_of = as_of or date.today()
    result = {
        "name": "snapshot",
        "severity": "ok",
        "checked": True,
        "expected": f"{as_of.isoformat()}_scores.json",
        "found_today": False,
        "latest": None,
        "latest_age_days": None,
        "rows": None,
        "detail": "",
    }

    dates: list[date] = []
    try:
        for f in Path(logs_dir).glob("*_scores.json"):
            d = parse_date_loose(f.name.split("_scores.json")[0])
            if d:
                dates.append(d)
    except OSError:
        dates = []

    today_file = Path(logs_dir) / result["expected"]
    result["found_today"] = today_file.is_file()

    if dates:
        latest = max(dates)
        result["latest"] = latest.isoformat()
        result["latest_age_days"] = (as_of - latest).days

    if result["found_today"]:
        try:
            payload = json.loads(today_file.read_text(encoding="utf-8"))
            result["rows"] = len(payload) if isinstance(payload, list) else None
        except (OSError, ValueError):
            result["rows"] = None
            result["severity"] = "warn"
            result["detail"] = f"{result['expected']} exists but is not readable JSON."
            return result
        if result["rows"] == 0:
            result["severity"] = "critical"
            result["detail"] = f"{result['expected']} was written but contains 0 stocks."
            return result
        result["detail"] = f"{result['expected']} written ({result['rows']} stocks)."
        return result

    # Weekends have no scheduled run, so a missing snapshot is only alarming if
    # the most recent one is itself stale.
    weekend = as_of.weekday() >= 5
    age = result["latest_age_days"]
    if weekend and age is not None and age <= 3:
        result["detail"] = (
            f"No snapshot today (weekend); latest is {result['latest']}."
        )
        return result

    result["severity"] = "critical"
    if result["latest"]:
        result["detail"] = (
            f"No score snapshot written today — latest is {result['latest']} "
            f"({_plural(age, 'day')} old)."
        )
    else:
        result["detail"] = f"No score snapshots found in {logs_dir}."
    return result


# --------------------------------------------------------------------------
# check 4 — universe-wide ratings_date freshness (the frozen-column detector)
# --------------------------------------------------------------------------

def check_ratings_freshness(rows: Optional[Iterable[dict]], as_of: date) -> dict:
    """The newest `ratings_date` anywhere in the universe should be recent.

    This is the check that would have caught the Jun-Aug outage even if
    `last_scraped` had kept advancing.
    """
    result = {
        "name": "ratings_freshness",
        "severity": "ok",
        "checked": False,
        "newest_ratings_date": None,
        "newest_age_days": None,
        "median_age_days": None,
        "missing": 0,
        "total_rows": 0,
        "detail": "",
    }
    if rows is None:
        result["detail"] = "Screener tab not read."
        return result

    rows = list(rows)
    result["checked"] = True
    result["total_rows"] = len(rows)

    parsed: list[date] = []
    for row in rows:
        d = parse_date_loose(row.get("ratings_date"))
        if d is None:
            result["missing"] += 1
        else:
            parsed.append(d)

    if not parsed:
        result["severity"] = "critical"
        result["detail"] = "No ticker has a readable ratings_date."
        return result

    newest = max(parsed)
    result["newest_ratings_date"] = newest.isoformat()
    result["newest_age_days"] = (as_of - newest).days
    result["median_age_days"] = int(
        statistics.median(sorted((as_of - d).days for d in parsed))
    )

    if result["newest_age_days"] > RATINGS_MAX_AGE_DAYS:
        result["severity"] = "critical"
        result["detail"] = (
            f"Newest ratings_date in the entire universe is {newest.isoformat()} "
            f"({_plural(result['newest_age_days'], 'day')} old) — "
            "the ratings_date pipeline looks frozen."
        )
    else:
        result["detail"] = (
            f"Newest ratings_date {newest.isoformat()} "
            f"({_plural(result['newest_age_days'], 'day')} old)."
        )
    return result


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

def _load_screener_rows() -> tuple[Optional[list[dict]], Optional[str]]:
    """Read the Screener tab. Returns (rows, error_message)."""
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv())
        from trader_agent.tools.loader import load_screener

        return load_screener(), None
    except Exception as exc:  # noqa: BLE001 - health check must never crash
        return None, f"{exc.__class__.__name__}: {exc}"


def check_health(
    rows: Optional[Iterable[dict]] = None,
    as_of: Optional[date] = None,
    logs_dir: Path = DEFAULT_LOGS_DIR,
    log_path: Path = DEFAULT_LOG_PATH,
    fetch_rows: bool = True,
) -> dict:
    """Run every check and return a structured report.

    `rows` are the Screener tab dicts. Pass them in when you already have them;
    otherwise they are fetched from Sheets unless `fetch_rows=False`.
    """
    as_of = as_of or date.today()
    fetch_error = None
    if rows is None and fetch_rows:
        rows, fetch_error = _load_screener_rows()

    checks = {
        "scrape_freshness": check_scrape_freshness(rows, as_of),
        "last_run": check_last_run(log_path, as_of),
        "snapshot": check_snapshot(logs_dir, as_of),
        "ratings_freshness": check_ratings_freshness(rows, as_of),
    }

    problems = [
        _problem(c["name"], c["severity"], c["detail"])
        for c in checks.values()
        if c["severity"] != "ok"
    ]
    if fetch_error:
        problems.insert(
            0,
            _problem(
                "sheet_access",
                "warn",
                f"Could not read the Screener tab — freshness unverified ({fetch_error}).",
            ),
        )

    status = "ok"
    for p in problems:
        if SEVERITY_RANK[p["severity"]] > SEVERITY_RANK[status]:
            status = p["severity"]

    return {
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "alarm": status != "ok",
        "headline": _headline(status, checks, problems),
        "problems": problems,
        "checks": checks,
    }


def _headline(status: str, checks: dict, problems: list[dict]) -> str:
    if status == "ok":
        return "Pipeline healthy — Screener data is fresh."
    fresh = checks["scrape_freshness"]
    if fresh["severity"] == "critical" and fresh.get("median_age_days") is not None:
        return (
            f"STALE DATA — Screener last updated "
            f"{_plural(fresh['median_age_days'], 'day')} ago "
            f"({fresh['stale_tickers']} of {fresh['total_rows']} tickers affected)"
        )
    critical = [p for p in problems if p["severity"] == "critical"]
    if critical:
        return f"PIPELINE PROBLEM — {critical[0]['message']}"
    return f"PIPELINE WARNING — {problems[0]['message']}"


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------

_STATUS_ICON = {"ok": "✅", "warn": "⚠️", "critical": "🚨"}


def render_text(report: dict) -> str:
    """Plain-text report, for the terminal and for alert emails."""
    icon = _STATUS_ICON.get(report["status"], "?")
    lines = [
        f"{icon} ms_screener pipeline health — {report['as_of']}",
        "=" * 62,
        report["headline"],
        "",
    ]
    if report["problems"]:
        lines.append("Problems:")
        for p in report["problems"]:
            lines.append(f"  {_STATUS_ICON.get(p['severity'], '-')} [{p['check']}] {p['message']}")
        lines.append("")

    checks = report.get("checks") or {}
    lines.append("Checks:")
    for name, check in checks.items():
        mark = _STATUS_ICON.get(check.get("severity"), "-")
        lines.append(f"  {mark} {check.get('name', name)}: {check.get('detail') or 'no detail'}")

    fresh = checks.get("scrape_freshness") or {}
    if fresh.get("median_age_days") is not None:
        lines += [
            "",
            f"  rows={fresh['total_rows']} "
            f"median_age={fresh['median_age_days']}d "
            f"max_age={fresh['max_age_days']}d "
            f"stale={fresh['stale_tickers']} "
            f"newest={fresh['newest_scrape']}",
        ]
    return "\n".join(lines)


def render_banner_html(report: Optional[dict]) -> str:
    """Loud red banner for the top of the morning brief. Empty string if healthy."""
    if not report or not report.get("alarm"):
        return ""

    critical = report["status"] == "critical"
    border = "#cf222e" if critical else "#bf8700"
    bg = "#fff5f5" if critical else "#fff8e5"
    icon = "🚨" if critical else "⚠️"
    title = (
        "DATA STALENESS ALARM — DO NOT TRADE OFF THIS BRIEF"
        if critical
        else "DATA HEALTH WARNING — verify before acting"
    )

    items = "".join(
        f"<li style='margin-bottom:6px'>"
        f"<strong style='text-transform:uppercase;font-size:11px;letter-spacing:.4px;color:{border}'>"
        f"{p['check'].replace('_', ' ')}</strong><br>{p['message']}</li>"
        for p in report["problems"]
    )

    checks = report.get("checks") or {}
    fresh = checks.get("scrape_freshness") or {}
    facts = []
    if fresh.get("median_age_days") is not None:
        facts.append(
            f"median <code>last_scraped</code> is <strong>{fresh['median_age_days']}d</strong> old "
            f"({fresh['newest_scrape']} newest)"
        )
        facts.append(
            f"<strong>{fresh['stale_tickers']}</strong> of "
            f"<strong>{fresh['total_rows']}</strong> tickers stale"
        )
    ratings = checks.get("ratings_freshness") or {}
    if ratings.get("newest_age_days") is not None:
        facts.append(
            f"newest <code>ratings_date</code> anywhere: "
            f"{ratings['newest_ratings_date']} ({ratings['newest_age_days']}d)"
        )
    run = checks.get("last_run") or {}
    if run.get("last_run"):
        facts.append(
            f"last ms_screener run: {run['last_run']} ({run['last_run_status']})"
        )
    facts_html = (
        "<p style='margin:10px 0 0 0;font-size:12px;color:#57606a'>"
        + " &nbsp;•&nbsp; ".join(facts)
        + "</p>"
    ) if facts else ""

    return f"""
  <div style="background:{bg};border:3px solid {border};border-radius:6px;
              padding:16px 18px;margin-bottom:16px">
    <div style="font-size:17px;font-weight:800;color:{border};margin-bottom:8px">
      {icon} {title}
    </div>
    <ul style="margin:0;padding-left:20px;font-size:13px;line-height:1.55;color:#24292f">
      {items}
    </ul>
    {facts_html}
    <p style="margin:10px 0 0 0;font-size:12px;color:#57606a">
      Diagnose: <code>python -m trader_agent.tools.health</code> &nbsp;•&nbsp;
      Log: <code>{run.get('log_path', DEFAULT_LOG_PATH)}</code>
    </p>
  </div>"""


def subject_prefix(report: Optional[dict]) -> str:
    """Prefix for the brief's email subject so staleness is visible in the inbox list."""
    if not report or not report.get("alarm"):
        return ""
    return "🚨 STALE DATA — " if report["status"] == "critical" else "⚠️ CHECK DATA — "


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    parser.add_argument("--no-fetch", action="store_true", help="skip the Google Sheets read")
    parser.add_argument("--email", action="store_true", help="email the report if unhealthy")
    parser.add_argument("--asof", help="evaluate as of YYYY-MM-DD (testing)")
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR))
    args = parser.parse_args(argv)

    as_of = parse_date_loose(args.asof) if args.asof else None
    report = check_health(
        as_of=as_of,
        logs_dir=Path(args.logs_dir),
        log_path=Path(args.log_path),
        fetch_rows=not args.no_fetch,
    )

    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(render_text(report))

    if args.email and report["alarm"]:
        from ms_screener.src.notify import send_plain_alert

        sent = send_plain_alert(
            subject=f"🚨 ms_screener pipeline unhealthy — {report['as_of']}",
            body=render_text(report),
        )
        print(f"alert email sent: {sent}", file=sys.stderr)

    return 1 if report["alarm"] else 0


if __name__ == "__main__":
    sys.exit(main())
