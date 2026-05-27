"""Generate morning brief and email it."""

import os
import smtplib
from dataclasses import asdict
from email.message import EmailMessage

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from trader_agent.tools.loader import load_fmv_history, load_screener
from trader_agent.tools.scorer import ScoredStock, score_all
from trader_agent.tools.signals import apply_fmv_flags, detect_fmv_upgrades, flag_stale


_CONVICTION_EMOJI = {
    "STRONG BUY": "🟢",
    "BUY": "🔵",
    "WATCH": "🟡",
    "SKIP": "⚪",
}


def build_brief(scored: list[ScoredStock], upgrades: dict[str, float]) -> tuple[str, str]:
    """Return (text_body, html_body) for the morning brief."""
    actionable = [s for s in scored if s.conviction in ("STRONG BUY", "BUY")]
    watch = [s for s in scored if s.conviction == "WATCH"]

    def _row_text(s: ScoredStock) -> str:
        disc = f"{s.discount_pct*100:.1f}%"
        age = f"{s.ratings_age_days}d" if s.ratings_age_days is not None else "?"
        fmv_flag = " ⬆FMV" if s.fmv_upgraded else ""
        stale_flag = " ⚠" if s.stale_rating else ""
        return f"  {s.ticker:<6} {s.conviction:<11} {s.stars}★  {disc:<7} {age}{fmv_flag}{stale_flag}"

    lines = ["Morning Brief", "=" * 50, ""]
    if actionable:
        lines.append("ACTIONABLE")
        lines.extend(_row_text(s) for s in actionable)
        lines.append("")
    if watch:
        lines.append("WATCH")
        lines.extend(_row_text(s) for s in watch)
    text_body = "\n".join(lines)

    def _html_row(s: ScoredStock) -> str:
        emoji = _CONVICTION_EMOJI.get(s.conviction, "")
        disc = f"{s.discount_pct*100:.1f}%"
        age = f"{s.ratings_age_days}d" if s.ratings_age_days is not None else "?"
        fmv_flag = " ⬆" if s.fmv_upgraded else ""
        stale_flag = " ⚠" if s.stale_rating else ""
        hint = s.sizing_hint or ""
        price = f"<br><small style='color:#888'>${s.last_price:,.2f}</small>" if s.last_price else ""
        return (
            f"<tr>"
            f"<td><strong>{s.ticker}</strong>{price}</td>"
            f"<td>{emoji} {s.conviction}</td>"
            f"<td>{'★' * s.stars}</td>"
            f"<td>{disc}</td>"
            f"<td>{age}{stale_flag}</td>"
            f"<td>{s.moat}</td>"
            f"<td>{s.uncertainty}</td>"
            f"<td>{hint}{fmv_flag}</td>"
            f"</tr>"
        )

    th = "<th>{}</th>"
    headers = "".join(th.format(h) for h in ["Ticker", "Conviction", "Stars", "Discount", "Age", "Moat", "Uncertainty", "Hint"])
    rows_html = "".join(_html_row(s) for s in actionable + watch)
    upgrade_note = ""
    if upgrades:
        items = ", ".join(f"{t} (+{p:.1f}%)" for t, p in upgrades.items())
        upgrade_note = f"<p><strong>FMV Upgrades (60d):</strong> {items}</p>"

    html_body = f"""\
<html><body>
<h2>Morning Brief</h2>
{upgrade_note}
<table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse;font-family:sans-serif;font-size:13px;">
  <thead><tr style="background:#f0f0f0;">{headers}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
</body></html>"""

    return text_body, html_body


def send_brief(text_body: str, html_body: str) -> None:
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    username = os.environ["EMAIL_USERNAME"]
    password = os.environ["EMAIL_PASSWORD"]
    recipients = [e.strip() for e in os.environ.get("ALERT_EMAILS", username).split(",")]

    msg = EmailMessage()
    msg["Subject"] = "📈 Morning Brief"
    msg["From"] = username
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_server, smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    print(f"Sent to: {', '.join(recipients)}")


if __name__ == "__main__":
    sheet_id = os.environ["SHEET_ID"]
    rows = load_screener(sheet_id)
    scored = score_all(rows)
    history = load_fmv_history(sheet_id)
    upgrades = detect_fmv_upgrades(history)
    scored = flag_stale(scored)
    scored = apply_fmv_flags(scored, upgrades)

    text_body, html_body = build_brief(scored, upgrades)
    print(text_body)
    send_brief(text_body, html_body)
