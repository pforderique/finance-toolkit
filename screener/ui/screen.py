"""Common screen utilities."""

from typing import Callable
import enum

import curses.ascii
import npyscreen


class Screen(enum.StrEnum):
    """Enum for screen names used in the application."""

    MAIN = "MAIN"
    STOCK_SCREENER = "STOCK_SCREENER"


def add_close_button(form: npyscreen.FormBaseNew):
    """
    Adds a red 'X' button to the top-right of any form that exits the app.
    """
    if form.columns is None:
        raise ValueError("Form must have a defined number of columns.")

    right_edge_offset = 8

    form.add(
        npyscreen.ButtonPress,
        name="X",
        when_pressed_function=lambda: form.parentApp.switchForm(None),
        relx=form.columns - right_edge_offset,
        rely=1,
        color="DANGER",
    )


def add_back_button(form: npyscreen.FormBaseNew, on_click: Callable) -> None:
    """Adds a 'Back' button to the top-right of the form."""
    if form.columns is None:
        raise ValueError("Form must have a defined number of columns.")

    form.add(
        npyscreen.ButtonPress,
        name="Back",
        when_pressed_function=on_click,
        relx=form.columns - 15,
        rely=1,
    )


def add_ctrl_c_handler(form: npyscreen.FormBaseNew):
    """
    Adds a handler for Ctrl-C to exit the application.
    """
    form.add_handlers({
        curses.ascii.ETX: lambda _: form.parentApp.switchForm(None)
    })
