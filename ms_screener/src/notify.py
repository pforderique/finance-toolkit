"""Fail-loud notifications for the ms_screener scheduled job.

The LaunchAgent job writes stdout to `~/Library/Logs/ms_screener.log` and stderr
to `~/Library/Logs/ms_screener.err`. Nobody reads those files, which is exactly
how a five-week outage went unnoticed in mid-2026. A run that dies must reach
the same inbox the morning brief lands in.

This deliberately reuses the SMTP credentials the rest of the toolkit already
uses (`EMAIL_USERNAME` / `EMAIL_PASSWORD` / `ALERT_EMAILS`) — no new channel, no
new service. If credentials are missing, we degrade to a stderr banner rather
than swallowing the failure or crashing the caller a second time.
"""

from __future__ import annotations

import os
import smtplib
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage
from typing import Iterable, Optional

SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))

#: How many trailing log lines to attach so the cause is visible in the email.
LOG_TAIL_LINES = 40


def _ensure_env() -> None:
    """Load .env lazily — the LaunchAgent starts with a near-empty environment."""
    if os.getenv("EMAIL_USERNAME"):
        return
    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except Exception:  # noqa: BLE001 - alerting must never crash the job
        pass


def _recipients() -> list[str]:
    username = os.getenv("EMAIL_USERNAME")
    raw = os.getenv("ALERT_EMAILS") or username or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def send_plain_alert(
    subject: str,
    body: str,
    html: Optional[str] = None,
    recipients: Optional[Iterable[str]] = None,
) -> bool:
    """Send an alert email. Returns True if it went out.

    Never raises: an alerting failure must not mask the original failure.
    """
    _ensure_env()
    username = os.getenv("EMAIL_USERNAME")
    password = os.getenv("EMAIL_PASSWORD")
    to = list(recipients) if recipients else _recipients()

    if not (username and password and to):
        print(
            "[notify] EMAIL_USERNAME/EMAIL_PASSWORD/ALERT_EMAILS not configured — "
            "alert not emailed.",
            file=sys.stderr,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - alerting must never crash the job
        print(f"[notify] failed to send alert email: {exc!r}", file=sys.stderr)
        return False

    print(f"[notify] alert emailed to: {', '.join(to)}", file=sys.stderr)
    return True


def _log_tail(path: str, lines: int = LOG_TAIL_LINES) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except OSError:
        return ""


def notify_run_failure(
    exc: BaseException,
    command: str = "ms_screener.main",
    log_path: str = "~/Library/Logs/ms_screener.log",
    err_path: str = "~/Library/Logs/ms_screener.err",
) -> bool:
    """Announce that a scheduled ms_screener run died.

    Called from the CLI's failure path so the user learns about it the same
    morning instead of five weeks later.
    """
    stamp = datetime.now().isoformat(sep=" ", timespec="seconds")
    log_path = os.path.expanduser(log_path)
    err_path = os.path.expanduser(err_path)

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    body = f"""ms_screener RUN FAILED

When:    {stamp}
Command: {command}
Error:   {exc}

The Screener tab was NOT refreshed. Today's morning brief will be scored off
stale data — the brief itself will carry a red staleness banner.

Check the pipeline:
    python -m trader_agent.tools.health

Re-run by hand:
    cd /Users/pfo/ws/finance/finance-toolkit
    uv run python -m ms_screener.main --auto --scrape-individual --scrape-max-stocks 200

--- traceback ---
{tb}
--- last {LOG_TAIL_LINES} lines of {log_path} ---
{_log_tail(log_path)}
--- last {LOG_TAIL_LINES} lines of {err_path} ---
{_log_tail(err_path)}
"""
    return send_plain_alert(
        subject=f"🚨 ms_screener FAILED — {stamp[:10]}",
        body=body,
    )
