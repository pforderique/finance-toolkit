"""The alerting module handles stock price alerts based on user-defined criteria."""

from collections.abc import Iterable, Sequence
from email.message import EmailMessage
import smtplib

from screener import config
from screener.service import stock_api


StockAPI = stock_api.StockAPI
StockInfo = stock_api.StockInfo
_NA = "N/A"


class StockRow(StockInfo):
    """A row of stock data with additional properties for alerting."""
    action: str


def get_action(stock_data: StockInfo) -> str:
    """
    Determine the action to take based on stock data.

    Args:
        stock_data: The stock data containing price and other metrics.

    Returns:
        str: Action to take ('BUY', 'SELL', 'HOLD').
    """
    if stock_data.discount is None:
        return _NA

    thresholds_by_uncertainty = {
        "Very Low": (0.95, 1.1),
        "Low": (0.9, 1.15),
        "Medium": (0.8, 1.2),
        "High": (0.75, 1.25),
        "Very High": (0.7, 1.3),
    }
    default_thresholds = (0.8, 1.2)

    if stock_data.uncertainty not in thresholds_by_uncertainty:
        lower, upper = default_thresholds
    else:
        lower, upper = thresholds_by_uncertainty.get(
            stock_data.uncertainty, default_thresholds
        )

    if stock_data.discount <= lower:
        return "BUY"

    if stock_data.discount >= upper:
        return "SELL"

    return "HOLD"


def send_alerts(
    alert_emails: Iterable[str],
    stock_infos: Sequence[StockInfo],
    failed_symbols: Iterable[str] | None = None
) -> str:
    """
    Send a plaintext email listing each alerted stock's ticker, action,
    discount, and star rating to the given list of email addresses.

    Requires the following in config.py:
    - EMAIL_SMTP_SERVER
    - EMAIL_SMTP_PORT
    - EMAIL_USERNAME
    - EMAIL_PASSWORD
    """
    def sort_by_action(s: StockRow) -> tuple:
        """Sort by action, then rating, then discount, then by ticker."""
        action_order = {"SELL": 0, "BUY": 1, "HOLD": 2, _NA: 3}
        discount_order = - \
            s.discount if s.discount is not None else float("inf")
        star_order = s.starRating if s.starRating is not None else float("inf")
        if s.action == "BUY":
            discount_order = -discount_order
            star_order = -star_order
        return (action_order.get(s.action, 3), star_order, discount_order, s.ticker)

    stock_rows = sorted(
        (StockRow(**s.model_dump(), action=get_action(s)) for s in stock_infos),
        key=sort_by_action
    )

    # Text (fallback for email clients that don't support HTML)
    text_lines = [
        "Stock Alerts",
        "=" * 50,
        f"{'Ticker':<8}  {'Action':<6}  {'Discount':<8}  {'Rating':<6}"
    ]
    for s in stock_rows:
        stars = "⭐"*s.starRating if s.starRating is not None else _NA
        text_lines.append(
            f"{s.ticker:<8}  {s.action:<6}  {s.discount or _NA:<8}  {stars:<6}"
        )
    text_body = "\n".join(text_lines)

    # HTML version
    html_rows = []
    for s in stock_rows:
        discount = f"{s.discount:.2f}" if s.discount is not None else _NA
        stars = "⭐" * s.starRating if s.starRating is not None else _NA
        day_change_per = f"{s.dayChangePer:.2f}%" if s.dayChangePer is not None else _NA
        html_rows.append(f"""
            <tr>
              <td><strong>{s.ticker}</strong></td>
              <td>{day_change_per}</td>
              <td>{s.action}</td>
              <td>{discount}</td>
              <td>{stars}</td>
            </tr>
        """)
    html_body = f"""\
    <html>
      <body>
        <h2>Today's Trades</h2>
        <table border="1" cellpadding="4" cellspacing="0"
               style="border-collapse: collapse; font-family: sans-serif;">
          <thead>
            <tr style="background-color: #f0f0f0;">
              <th>Ticker</th>
              <th>Day %</th>
              <th>Action</th>
              <th>Discount</th>
              <th>Rating</th>
            </tr>
          </thead>
          <tbody>
            {''.join(html_rows)}
          </tbody>
        </table>
        {'<p>Failed symbols: ' + ', '.join(failed_symbols) + '</p>' if failed_symbols else ''}
      </body>
    </html>
    """

    # --- 3) Compose & send ---
    msg = EmailMessage()
    msg["Subject"] = "🚨 Stock Alert"
    msg["From"] = config.EMAIL_USERNAME
    msg["To"] = ", ".join(alert_emails)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(config.EMAIL_SMTP_SERVER, config.EMAIL_SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.EMAIL_USERNAME, config.EMAIL_PASSWORD)
        smtp.send_message(msg)

    return html_body
