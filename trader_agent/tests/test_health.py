"""Tests for the pipeline staleness alarm (trader_agent.tools.health)."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from trader_agent.tools import health
from trader_agent.tools.health import (
    business_days_between,
    check_health,
    check_last_run,
    check_ratings_freshness,
    check_scrape_freshness,
    check_snapshot,
    parse_date_loose,
    parse_run_log,
    render_banner_html,
    render_text,
    subject_prefix,
)

# A Monday, so weekday/weekend behaviour is deterministic.
MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 7)
SATURDAY = date(2026, 8, 8)


def _rows(scrape_dates, ratings_dates=None, n=10):
    """Build Screener-shaped rows. `scrape_dates` may be one date or a list."""
    if isinstance(scrape_dates, date):
        scrape_dates = [scrape_dates] * n
    if ratings_dates is None:
        ratings_dates = [MONDAY - timedelta(days=3)] * len(scrape_dates)
    elif isinstance(ratings_dates, date):
        ratings_dates = [ratings_dates] * len(scrape_dates)
    return [
        {
            "ticker": f"T{i}",
            "last_scraped": s.strftime("%-m/%-d/%Y"),
            "ratings_date": r.strftime("%b %-d, %Y"),
        }
        for i, (s, r) in enumerate(zip(scrape_dates, ratings_dates))
    ]


def _log(*runs) -> str:
    """Build an ms_screener stdout log from (timestamp, body) pairs."""
    out = []
    for stamp, body in runs:
        out.append("╭─────────────────────────────────────╮")
        out.append(f"│ M* Workflow  •  {stamp} │")
        out.append("╰─────────────────────────────────────╯")
        out.append(body)
    return "\n".join(out)


_OK_BODY = """─── Read Collected Data Tab ───
done.
─── Summary ───
 Files processed     3
 Rows ingested       102
 Snapshot rows       102
"""

_ERR_BODY = """─── Auto Download ───
• Fetching link 1/2
Error: Timed out waiting for Morningstar CSV to finish downloading.
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-08-10", date(2026, 8, 10)),
    ("8/10/2026", date(2026, 8, 10)),
    ("08/10/2026", date(2026, 8, 10)),
    ("Aug 7, 2026", date(2026, 8, 7)),
    ("August 7, 2026", date(2026, 8, 7)),
    ("2026-08-10T07:05:03", date(2026, 8, 10)),
    ("", None),
    (None, None),
    ("garbage", None),
])
def test_parse_date_loose(raw, expected):
    assert parse_date_loose(raw) == expected


@pytest.mark.parametrize("start,end,expected", [
    (FRIDAY, MONDAY, 1),                 # Fri -> Mon is ONE business day
    (FRIDAY, date(2026, 8, 11), 2),      # Fri -> Tue
    (FRIDAY, date(2026, 8, 12), 3),      # Fri -> Wed
    (FRIDAY, SATURDAY, 0),
    (MONDAY, MONDAY, 0),
    (MONDAY, FRIDAY, 0),                 # negative span
])
def test_business_days_between(start, end, expected):
    assert business_days_between(start, end) == expected


# --------------------------------------------------------------------------
# check 1 — scrape freshness
# --------------------------------------------------------------------------

def test_scrape_fresh_today_is_ok():
    r = check_scrape_freshness(_rows(MONDAY), MONDAY)
    assert r["severity"] == "ok"
    assert r["median_age_days"] == 0
    assert r["stale_tickers"] == 0


def test_friday_scrape_read_on_monday_is_not_stale():
    """The normal weekend gap is exactly 3 days — it must not cry wolf."""
    r = check_scrape_freshness(_rows(FRIDAY), MONDAY)
    assert r["severity"] == "ok"
    assert r["median_age_days"] == 3


def test_long_weekend_is_not_stale():
    """Fri scrape + Monday holiday + Tuesday brief = 4 calendar days, 1 missed run."""
    r = check_scrape_freshness(_rows(FRIDAY), date(2026, 8, 11))
    assert r["median_age_days"] == 4
    assert r["business_days"] == 2
    assert r["severity"] == "ok"


def test_two_missed_runs_trips_the_alarm():
    """Fri scrape read on Wednesday = 2 consecutive missed runs. Shout."""
    r = check_scrape_freshness(_rows(FRIDAY), date(2026, 8, 12))
    assert r["severity"] == "critical"
    assert r["business_days"] == 3


def test_five_week_outage_is_critical_with_counts():
    """The actual Jun-Aug 2026 failure."""
    rows = _rows(date(2026, 6, 26), n=102)
    r = check_scrape_freshness(rows, date(2026, 8, 6))
    assert r["severity"] == "critical"
    assert r["median_age_days"] == 41
    assert r["stale_tickers"] == 102
    assert r["total_rows"] == 102
    assert "41 days old" in r["detail"]
    assert "102 of 102 tickers stale" in r["detail"]


def test_median_ignores_a_few_fresh_outliers():
    """Two hand-refreshed tickers must not mask 98 frozen ones."""
    rows = _rows([MONDAY, MONDAY] + [date(2026, 6, 26)] * 98)
    r = check_scrape_freshness(rows, MONDAY)
    assert r["severity"] == "critical"
    assert r["stale_tickers"] == 98


def test_mixed_date_formats_all_parse():
    rows = [
        {"ticker": "A", "last_scraped": "8/10/2026"},
        {"ticker": "B", "last_scraped": "2026-08-10"},
    ]
    r = check_scrape_freshness(rows, MONDAY)
    assert r["unparseable"] == 0
    assert r["median_age_days"] == 0


def test_unparseable_dates_warn():
    rows = [{"ticker": f"T{i}", "last_scraped": "???"} for i in range(5)]
    r = check_scrape_freshness(rows, MONDAY)
    assert r["severity"] == "critical"
    assert "No usable last_scraped" in r["detail"]


def test_empty_sheet_is_critical():
    assert check_scrape_freshness([], MONDAY)["severity"] == "critical"


def test_rows_none_is_not_checked():
    r = check_scrape_freshness(None, MONDAY)
    assert r["checked"] is False
    assert r["severity"] == "ok"


# --------------------------------------------------------------------------
# check 2 — run log
# --------------------------------------------------------------------------

def test_parse_run_log_attributes_error_to_the_run_above_it():
    text = _log(
        ("2026-08-06 07:05:02", _ERR_BODY),
        ("2026-08-07 07:05:04", _OK_BODY),
    )
    runs = parse_run_log(text)
    assert [r["status"] for r in runs] == ["error", "ok"]
    assert "Timed out" in runs[0]["error"]
    assert runs[1]["error"] is None


def test_run_with_neither_error_nor_summary_is_incomplete():
    runs = parse_run_log(_log(("2026-08-10 07:05:00", "• Fetching link 1/2\n")))
    assert runs[0]["status"] == "incomplete"


def test_last_run_ok(tmp_path):
    p = tmp_path / "ms.log"
    p.write_text(_log(("2026-08-10 07:05:02", _OK_BODY)), encoding="utf-8")
    r = check_last_run(p, MONDAY)
    assert r["severity"] == "ok"
    assert r["last_run_status"] == "ok"
    assert r["consecutive_failures"] == 0


def test_last_run_errored_is_critical_and_counts_the_streak(tmp_path):
    p = tmp_path / "ms.log"
    p.write_text(
        _log(
            ("2026-08-05 07:05:03", _ERR_BODY),
            ("2026-08-06 07:05:02", _ERR_BODY),
            ("2026-08-07 07:05:04", _ERR_BODY),
        ),
        encoding="utf-8",
    )
    r = check_last_run(p, MONDAY)
    assert r["severity"] == "critical"
    assert r["consecutive_failures"] == 3
    assert "3 consecutive failed runs" in r["detail"]
    assert "Timed out" in r["detail"]


def test_no_run_for_days_is_critical(tmp_path):
    """LaunchAgent unloaded / machine asleep — the log just stops."""
    p = tmp_path / "ms.log"
    p.write_text(_log(("2026-08-03 07:05:00", _OK_BODY)), encoding="utf-8")
    r = check_last_run(p, MONDAY)
    assert r["severity"] == "critical"
    assert "has not run since" in r["detail"]


def test_friday_run_read_on_monday_is_ok(tmp_path):
    p = tmp_path / "ms.log"
    p.write_text(_log(("2026-08-07 07:05:00", _OK_BODY)), encoding="utf-8")
    assert check_last_run(p, MONDAY)["severity"] == "ok"


def test_missing_log_file_warns(tmp_path):
    r = check_last_run(tmp_path / "nope.log", MONDAY)
    assert r["severity"] == "warn"
    assert r["checked"] is False


def test_log_with_no_run_headers_warns(tmp_path):
    p = tmp_path / "ms.log"
    p.write_text("nothing useful here\n", encoding="utf-8")
    assert check_last_run(p, MONDAY)["severity"] == "warn"


# --------------------------------------------------------------------------
# check 3 — snapshot
# --------------------------------------------------------------------------

def _write_snapshot(d: Path, day: date, n: int = 3):
    payload = [{"ticker": f"T{i}", "conviction": "BUY"} for i in range(n)]
    (d / f"{day.isoformat()}_scores.json").write_text(json.dumps(payload), encoding="utf-8")


def test_snapshot_present_today(tmp_path):
    _write_snapshot(tmp_path, MONDAY)
    r = check_snapshot(tmp_path, MONDAY)
    assert r["severity"] == "ok"
    assert r["found_today"] is True
    assert r["rows"] == 3


def test_missing_snapshot_on_weekday_is_critical(tmp_path):
    _write_snapshot(tmp_path, FRIDAY)
    r = check_snapshot(tmp_path, MONDAY)
    assert r["severity"] == "critical"
    assert r["latest"] == FRIDAY.isoformat()
    assert "No score snapshot written today" in r["detail"]


def test_missing_snapshot_on_weekend_is_tolerated(tmp_path):
    _write_snapshot(tmp_path, FRIDAY)
    r = check_snapshot(tmp_path, SATURDAY)
    assert r["severity"] == "ok"
    assert "weekend" in r["detail"]


def test_no_snapshots_at_all_is_critical(tmp_path):
    r = check_snapshot(tmp_path, MONDAY)
    assert r["severity"] == "critical"
    assert r["latest"] is None


def test_empty_snapshot_is_critical(tmp_path):
    (tmp_path / f"{MONDAY.isoformat()}_scores.json").write_text("[]", encoding="utf-8")
    r = check_snapshot(tmp_path, MONDAY)
    assert r["severity"] == "critical"
    assert "0 stocks" in r["detail"]


def test_corrupt_snapshot_warns(tmp_path):
    (tmp_path / f"{MONDAY.isoformat()}_scores.json").write_text("{not json", encoding="utf-8")
    assert check_snapshot(tmp_path, MONDAY)["severity"] == "warn"


# --------------------------------------------------------------------------
# check 4 — universe ratings_date freshness
# --------------------------------------------------------------------------

def test_ratings_fresh_is_ok():
    rows = _rows(MONDAY, ratings_dates=MONDAY - timedelta(days=2))
    assert check_ratings_freshness(rows, MONDAY)["severity"] == "ok"


def test_frozen_ratings_column_is_critical_even_when_scrape_is_fresh():
    """The nastier variant: last_scraped advances, ratings_date never does."""
    rows = _rows(MONDAY, ratings_dates=date(2026, 5, 29))
    assert check_scrape_freshness(rows, MONDAY)["severity"] == "ok"
    r = check_ratings_freshness(rows, MONDAY)
    assert r["severity"] == "critical"
    assert r["newest_ratings_date"] == "2026-05-29"
    assert "frozen" in r["detail"]


def test_ratings_uses_the_newest_not_the_median():
    """One fresh rating in the universe means the pipeline is alive."""
    rows = _rows(MONDAY, ratings_dates=[MONDAY] + [date(2025, 1, 1)] * 9)
    assert check_ratings_freshness(rows, MONDAY)["severity"] == "ok"


def test_no_ratings_at_all_is_critical():
    rows = [{"ticker": "A", "ratings_date": ""}]
    assert check_ratings_freshness(rows, MONDAY)["severity"] == "critical"


# --------------------------------------------------------------------------
# aggregation + rendering
# --------------------------------------------------------------------------

def _healthy_env(tmp_path, as_of=MONDAY):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_snapshot(logs, as_of)
    log = tmp_path / "ms.log"
    log.write_text(_log((f"{as_of.isoformat()} 07:05:02", _OK_BODY)), encoding="utf-8")
    return logs, log


def test_check_health_all_green(tmp_path):
    logs, log = _healthy_env(tmp_path)
    report = check_health(rows=_rows(MONDAY), as_of=MONDAY, logs_dir=logs, log_path=log)
    assert report["status"] == "ok"
    assert report["alarm"] is False
    assert report["problems"] == []
    assert render_banner_html(report) == ""
    assert subject_prefix(report) == ""


def test_check_health_stale_data(tmp_path):
    logs, log = _healthy_env(tmp_path)
    rows = _rows(date(2026, 6, 26), ratings_dates=date(2026, 5, 29), n=102)
    report = check_health(rows=rows, as_of=MONDAY, logs_dir=logs, log_path=log)
    assert report["status"] == "critical"
    assert report["alarm"] is True
    assert "STALE DATA" in report["headline"]
    assert {p["check"] for p in report["problems"]} >= {"scrape_freshness", "ratings_freshness"}


def test_check_health_previous_run_errored(tmp_path):
    logs, log = _healthy_env(tmp_path)
    log.write_text(_log((f"{MONDAY.isoformat()} 07:05:02", _ERR_BODY)), encoding="utf-8")
    report = check_health(rows=_rows(MONDAY), as_of=MONDAY, logs_dir=logs, log_path=log)
    assert report["status"] == "critical"
    assert [p["check"] for p in report["problems"]] == ["last_run"]


def test_check_health_missing_snapshot(tmp_path):
    logs, log = _healthy_env(tmp_path)
    (logs / f"{MONDAY.isoformat()}_scores.json").unlink()
    report = check_health(rows=_rows(MONDAY), as_of=MONDAY, logs_dir=logs, log_path=log)
    assert report["status"] == "critical"
    assert [p["check"] for p in report["problems"]] == ["snapshot"]


def test_check_health_reports_sheet_read_failure(tmp_path, monkeypatch):
    """A brief must never render clean just because it could not verify freshness."""
    logs, log = _healthy_env(tmp_path)
    monkeypatch.setattr(health, "_load_screener_rows", lambda: (None, "RuntimeError: boom"))
    report = check_health(as_of=MONDAY, logs_dir=logs, log_path=log, fetch_rows=True)
    assert report["alarm"] is True
    assert report["problems"][0]["check"] == "sheet_access"


def test_check_health_no_fetch_leaves_sheet_checks_unchecked(tmp_path):
    logs, log = _healthy_env(tmp_path)
    report = check_health(as_of=MONDAY, logs_dir=logs, log_path=log, fetch_rows=False)
    assert report["status"] == "ok"
    assert report["checks"]["scrape_freshness"]["checked"] is False


def test_banner_states_what_how_stale_and_how_many(tmp_path):
    logs, log = _healthy_env(tmp_path)
    rows = _rows(date(2026, 6, 26), ratings_dates=date(2026, 5, 29), n=102)
    report = check_health(rows=rows, as_of=MONDAY, logs_dir=logs, log_path=log)
    banner = render_banner_html(report)

    assert "DATA STALENESS ALARM" in banner
    assert "#cf222e" in banner                      # red
    assert "<strong>45d</strong> old" in banner     # HOW stale
    assert "102</strong> of" in banner              # HOW many tickers
    assert "last_scraped" in banner                 # WHAT is stale
    assert "2026-05-29" in banner                   # frozen ratings_date
    assert str(log) in banner                       # where to look


def test_banner_is_amber_for_warn_only():
    report = {
        "status": "warn",
        "alarm": True,
        "problems": [{"check": "sheet_access", "severity": "warn", "message": "unreachable"}],
        "checks": {},
    }
    banner = render_banner_html(report)
    assert "DATA HEALTH WARNING" in banner
    assert "#bf8700" in banner
    assert subject_prefix(report) == "⚠️ CHECK DATA — "


def test_banner_tolerates_a_partial_report():
    """The synthetic fallback report has empty check dicts — must still render."""
    assert "unverified" in render_banner_html({
        "status": "warn",
        "alarm": True,
        "problems": [{"check": "health_check", "severity": "warn", "message": "unverified"}],
        "checks": {"scrape_freshness": {}, "ratings_freshness": {}, "last_run": {}},
    })


def test_render_banner_none_and_healthy():
    assert render_banner_html(None) == ""
    assert render_banner_html({"alarm": False}) == ""


def test_render_text_includes_every_check(tmp_path):
    logs, log = _healthy_env(tmp_path)
    report = check_health(rows=_rows(MONDAY), as_of=MONDAY, logs_dir=logs, log_path=log)
    text = render_text(report)
    for name in ("scrape_freshness", "last_run", "snapshot", "ratings_freshness"):
        assert name in text


def test_cli_exit_code_signals_health(tmp_path, capsys):
    logs, log = _healthy_env(tmp_path)
    argv = ["--no-fetch", "--asof", MONDAY.isoformat(),
            "--logs-dir", str(logs), "--log-path", str(log)]
    assert health.main(argv) == 0

    log.write_text(_log((f"{MONDAY.isoformat()} 07:05:02", _ERR_BODY)), encoding="utf-8")
    assert health.main(argv) == 1
    assert "Timed out" in capsys.readouterr().out


def test_cli_json_output_is_parseable(tmp_path, capsys):
    logs, log = _healthy_env(tmp_path)
    health.main(["--json", "--no-fetch", "--asof", MONDAY.isoformat(),
                 "--logs-dir", str(logs), "--log-path", str(log)])
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"


# --------------------------------------------------------------------------
# brief integration
# --------------------------------------------------------------------------

def test_brief_puts_the_banner_above_what_changed():
    from trader_agent.tools.send_reasoned_brief import build_html

    report = {
        "status": "critical",
        "alarm": True,
        "problems": [{"check": "scrape_freshness", "severity": "critical", "message": "41 days old"}],
        "checks": {},
    }
    html = build_html({"date": "2026-08-06", "week_changes": {"changes": []}}, health=report)
    assert "DATA STALENESS ALARM" in html
    assert html.index("DATA STALENESS ALARM") < html.index("What Changed")


def test_brief_has_no_banner_when_healthy():
    from trader_agent.tools.send_reasoned_brief import build_html

    html = build_html({"date": "2026-08-06", "week_changes": {"changes": []}},
                      health={"alarm": False})
    assert "STALENESS ALARM" not in html
