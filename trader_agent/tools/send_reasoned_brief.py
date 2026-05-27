"""Render a trader agent JSON brief as a styled HTML email and send it.

Usage:
  python -m trader_agent.tools.send_reasoned_brief /tmp/trader_brief.json
"""

import json
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_MOAT_COLOR = {"Wide": "#1a7f37", "Narrow": "#d4a017", "None": "#cf222e"}
_UNCERTAINTY_COLOR = {
    "Low": "#1a7f37",
    "Medium": "#0969da",
    "High": "#d4a017",
    "Very High": "#cf222e",
}
_STARS = {5: "★★★★★", 4: "★★★★☆", 3: "★★★☆☆"}


def _badge(text: str, color: str) -> str:
    return (
        f"<span style='background:{color};color:#fff;border-radius:3px;"
        f"padding:1px 6px;font-size:11px;font-weight:600;white-space:nowrap'>{text}</span>"
    )


def _signal_table(stocks: list[dict], emoji: str, header_color: str) -> str:
    if not stocks:
        return ""

    rows = ""
    for s in stocks:
        moat_color = _MOAT_COLOR.get(s["moat"], "#888")
        unc_color = _UNCERTAINTY_COLOR.get(s["uncertainty"], "#888")
        stars = _STARS.get(s["stars"], "")
        age = f"{s['ratings_age_days']}d"

        # Ticker cell — link to Morningstar if perf_id available
        ms_url = s.get("morningstar_url") or ""
        ticker_label = (
            f"<a href='{ms_url}' style='color:inherit;text-decoration:none;font-weight:700'>{s['ticker']}</a>"
            if ms_url else f"<span style='font-weight:700'>{s['ticker']}</span>"
        )

        price_fmv = (
            f"<small style='color:#555'>${s['last_price']:,.2f} / FMV ${s['fmv']:,.0f}</small>"
            if s.get("last_price") and s.get("fmv") else ""
        )

        # Notes: analyst source + any warnings
        note_parts = []
        if s.get("verified") and s.get("analyst"):
            note_parts.append(f"✅ {s['analyst']}")
        elif not s.get("verified"):
            note_parts.append("Quant est.")
        raw_notes = s.get("notes") or ""
        if raw_notes:
            note_parts.append(raw_notes)
        notes_cell = "<br>".join(f"<span style='font-size:11px;color:#555'>{p}</span>" for p in note_parts)

        rows += f"""
        <tr style='border-bottom:1px solid #eee'>
          <td style='padding:8px 10px;white-space:nowrap'>
            {emoji} {ticker_label}<br>
            <small style='font-weight:400;color:#555'>{s['company']}</small><br>
            {price_fmv}
          </td>
          <td style='padding:8px 10px;text-align:center;white-space:nowrap'>{stars}</td>
          <td style='padding:8px 10px;text-align:center'>{_badge(s['moat'], moat_color)}</td>
          <td style='padding:8px 10px;text-align:center'>{_badge(s['uncertainty'], unc_color)}</td>
          <td style='padding:8px 10px;text-align:center;font-size:15px;font-weight:700;white-space:nowrap'>
            {100 - s['pct_of_fmv']:.0f}% off
          </td>
          <td style='padding:8px 10px;text-align:center;color:#555;font-size:12px;white-space:nowrap'>{age}</td>
          <td style='padding:8px 10px;font-size:12px;color:#333;white-space:nowrap'>{s['sizing_hint']}</td>
          <td style='padding:8px 10px;font-size:11px;max-width:200px'>{notes_cell}</td>
        </tr>"""

    return f"""
    <table style='border-collapse:collapse;width:100%;font-family:sans-serif;font-size:13px;margin-bottom:24px'>
      <thead>
        <tr style='background:{header_color};color:#fff'>
          <th style='padding:8px 10px;text-align:left'>Ticker</th>
          <th style='padding:8px 10px'>Stars</th>
          <th style='padding:8px 10px'>Moat</th>
          <th style='padding:8px 10px'>Uncertainty</th>
          <th style='padding:8px 10px'>Discount</th>
          <th style='padding:8px 10px'>Age</th>
          <th style='padding:8px 10px;text-align:left'>Sizing</th>
          <th style='padding:8px 10px;text-align:left'>Notes</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _reasoning_section(stocks: list[dict]) -> str:
    items = ""
    for s in stocks:
        if not s.get("reasoning"):
            continue
        emoji = "💚" if s.get("_conviction") == "STRONG BUY" else "🟢"

        # Sources list
        sources_html = ""
        for src in s.get("sources", []):
            url = src.get("url", "")
            title = src.get("title", url)
            src_date = src.get("date", "")
            date_str = f" <span style='color:#888'>({src_date})</span>" if src_date else ""
            if url:
                sources_html += f"<li><a href='{url}' style='color:#0969da'>{title}</a>{date_str}</li>"
            else:
                sources_html += f"<li>{title}{date_str}</li>"

        sources_block = (
            f"<ul style='margin:6px 0 0 0;padding-left:18px;font-size:11px;color:#555'>{sources_html}</ul>"
            if sources_html else ""
        )

        items += f"""
        <div style='margin-bottom:16px;padding:12px 16px;background:#f6f8fa;border-left:3px solid #0969da;border-radius:0 4px 4px 0'>
          <div style='font-weight:700;font-size:14px;margin-bottom:4px'>{emoji} {s['ticker']} — {s['company']}</div>
          <div style='color:#333;line-height:1.6'>{s['reasoning']}</div>
          {sources_block}
        </div>"""
    return items


def build_html(data: dict) -> str:
    today = data.get("date", date.today().strftime("%Y-%m-%d"))
    strong_buys = data.get("strong_buys", [])
    buys = data.get("buy", [])
    patterns = data.get("patterns", "")
    stats = data.get("stats", {})

    for s in strong_buys:
        s["_conviction"] = "STRONG BUY"
    for s in buys:
        s["_conviction"] = "BUY"

    sb_table = _signal_table(strong_buys, "💚", "#1a7f37")
    buy_table = _signal_table(buys, "🟢", "#0969da")
    reasoning_html = _reasoning_section(strong_buys + buys)

    if isinstance(stats, dict):
        stats_str = (
            f"Scored: {stats.get('scored','?')} &nbsp;|&nbsp; "
            f"💚 {stats.get('strong_buy','?')} strong buy &nbsp;|&nbsp; "
            f"🟢 {stats.get('buy','?')} buy &nbsp;|&nbsp; "
            f"🟡 {stats.get('watch','?')} watch &nbsp;|&nbsp; "
            f"Skipped: {stats.get('skipped','?')}"
        )
    else:
        stats_str = str(stats)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:960px;margin:0 auto;padding:24px;color:#24292f">

  <h2 style="border-bottom:2px solid #1a7f37;padding-bottom:8px;color:#1a7f37">
    📊 Morning Brief — {today}
  </h2>

  <h3 style="color:#1a7f37;margin-bottom:8px">💚 Strong Buy</h3>
  {sb_table if sb_table else '<p style="color:#888">None today.</p>'}

  <h3 style="color:#0969da;margin-bottom:8px">🟢 Buy</h3>
  {buy_table if buy_table else '<p style="color:#888">None today.</p>'}

  <h3 style="margin-bottom:8px">🔍 Why I included each</h3>
  {reasoning_html if reasoning_html else '<p style="color:#888">No reasoning provided.</p>'}

  <h3 style="margin-bottom:8px">📈 Patterns</h3>
  <p style="background:#f6f8fa;padding:12px 16px;border-radius:4px;line-height:1.6">{patterns}</p>

  <p style="color:#888;font-size:12px;border-top:1px solid #eee;padding-top:12px;margin-top:24px">
    {stats_str}
  </p>

</body>
</html>"""


_LOGS_DIR = Path(__file__).parent.parent / "logs"


def _archive_brief(data: dict, today: str) -> None:
    _LOGS_DIR.mkdir(exist_ok=True)
    dest = _LOGS_DIR / f"{today}_brief.json"
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: send_reasoned_brief <path_to_brief_json>", file=sys.stderr)
        sys.exit(1)

    data = json.loads(open(sys.argv[1], encoding="utf-8").read())
    html_body = build_html(data)

    today = data.get("date", date.today().strftime("%Y-%m-%d"))
    _archive_brief(data, today)
    username = os.environ["EMAIL_USERNAME"]
    password = os.environ["EMAIL_PASSWORD"]
    recipients = [e.strip() for e in os.environ.get("ALERT_EMAILS", username).split(",")]

    msg = EmailMessage()
    msg["Subject"] = f"📈 Morning Brief — {today}"
    msg["From"] = username
    msg["To"] = ", ".join(recipients)
    msg.set_content("Open in an HTML-capable email client to view the morning brief.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    print(f"Sent to: {', '.join(recipients)}")


if __name__ == "__main__":
    main()
