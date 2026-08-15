"""Console helpers (rich output formatting) for the Fidelity tool."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

if TYPE_CHECKING:  # pragma: no cover
    from fidelity.src.datamodel import ChangeEntry, ChangePlan

custom_theme = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "green",
        "header": "bold bright_cyan",
    }
)

console = Console(theme=custom_theme)


def humanize_age(then: datetime, now: Optional[datetime] = None) -> str:
    """Render the elapsed time between `then` and `now` (default: now) as a
    short relative string, e.g. "3 days ago", "2 hours ago", "just now"."""
    now = now or datetime.now(then.tzinfo)
    seconds = max((now - then).total_seconds(), 0.0)

    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = int(days // 30)
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = int(days // 365)
    return f"{years} year{'s' if years != 1 else ''} ago"


def print_header(text: str) -> None:
    """Print a header rule to the console."""
    console.rule(f"[header]{text}[/header]")


def timestamp() -> str:
    """Return the current timestamp as a formatted string."""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def print_warning(message: str) -> None:
    """Print a warning message to the console."""
    console.print(f"[warning]⚠ {message}[/warning]")


def print_error(message: str) -> None:
    """Print an error message to the console."""
    console.print(f"[error]✖ {message}[/error]")


def print_success(message: str) -> None:
    """Print a success message to the console."""
    console.print(f"[success]✔ {message}[/success]")


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def _fmt_change(old, new) -> str:
    if old is None and new is not None:
        return f"— → {_fmt_num(new)}"
    if new is None and old is not None:
        return f"{_fmt_num(old)} → —"
    return f"{_fmt_num(old)} → {_fmt_num(new)}"


def _fmt_currency(value: float) -> str:
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def render_diff_table(plan: "ChangePlan") -> None:
    """Render the dry-run diff table + footer. Never writes anything -- display only."""

    from fidelity.src.datamodel import equity_delta

    action_labels = {"add": "Add", "update": "Update", "delete": "Delete"}
    rows = [
        (equity_delta(entry), action_labels[entry.action], entry)
        for entry in plan.all_actionable()
    ]
    rows.sort(key=lambda item: abs(item[0]), reverse=True)

    table = Table(title="Portfolio sync plan (dry run)", show_lines=False, show_header=True)
    table.add_column("Action")
    table.add_column("Ticker")
    table.add_column("Account")
    table.add_column("Shares (old → new)")
    table.add_column("Avg_Cost (old → new)")
    table.add_column("Equity Δ", justify="right")
    table.add_column("Row", justify="right")

    for delta, action_label, entry in rows:
        color = "green" if delta > 0 else "red" if delta < 0 else None
        equity_text = _fmt_currency(delta)
        if color:
            equity_text = f"[{color}]{equity_text}[/{color}]"
        table.add_row(
            action_label,
            entry.ticker,
            entry.account_label,
            _fmt_change(entry.old_shares, entry.new_shares),
            _fmt_change(entry.old_avg_cost, entry.new_avg_cost),
            equity_text,
            str(entry.row_number) if entry.row_number is not None else "(new)",
        )

    if rows:
        console.print(table)
    else:
        print_success("No changes: sheet already matches the CSV for owned/observed accounts")

    counts = plan.counts()
    net_delta = plan.net_equity_delta()
    summary = (
        f"{counts['adds']} adds, {counts['updates']} updates, {counts['deletes']} deletes, "
        f"{counts['unchanged']} unchanged, {counts['untouched']} untouched (non-Fidelity)"
    )
    console.print(f"[cyan]{summary}[/cyan]")

    color = "green" if net_delta > 0 else "red" if net_delta < 0 else None
    net_text = f"Net equity delta: {_fmt_currency(net_delta)}"
    if color:
        console.print(f"[{color}]{net_text}[/{color}]")
    else:
        console.print(net_text)
