"""Logging configuration and console helpers for the Fidelity tool."""

import logging
from datetime import datetime

from rich.console import Console
from rich.theme import Theme

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


def configure_logging(level: str) -> None:
    """Configure structured logging for the CLI."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


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
