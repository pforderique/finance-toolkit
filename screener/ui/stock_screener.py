"""Stock Screener Table UI Page."""

from collections.abc import Iterable
import dataclasses
from datetime import datetime
import functools
from typing import Any

import npyscreen

from screener.ui import screen
from screener.service import stock_api
from screener.service import alerting


CacheOption = stock_api.CacheOption
StockAPI = stock_api.StockAPI
StockInfo = stock_api.StockInfo

_COLUMN_NAMES = [
    "Company",
    "Ticker",
    "Day %",
    "Price",
    "Fair Value",
    "Discount",
    "Rating",
    "Uncertainty",
    "Action",
    "Rating Date",
    "Refresh Date",
]
_STOCK_ROW_FIELD_BY_HEADER_IDX = [
    "name",
    "ticker",
    "dayChangePer",
    "lastPrice",
    "latestFairValue",
    "discount",
    "starRating",
    "uncertainty",
    "action",
    "fairValueDate",
    "lastCachedDate",
]
_UNCERTAINTIES_ORDER = ["Very Low", "Low", "Medium", "High", "Very High"]
_ACTIONS_ORDER = ["N/A", "SELL", "HOLD", "BUY"]
_INSTRUCTION_COLOR = "STANDOUT"
_HEADER_COLOR = "CONTROL"
_ACTIVE_COLOR = "GOOD"
_NOT_AVAILABLE = "N/A"


class StockRow(StockInfo):
    """Data structure for holding stock information in a row format."""

    action: str


@dataclasses.dataclass
class ScreenerTable:
    """Data structure for holding formatted screener table data."""
    headers: list[str]
    rows: list[list[str]]
    raw_data: list[StockRow]

    def sort_by(self, field: str, reverse: bool = False):
        """
        Sort the table by a specific field.

        Args:
            field: The field to sort by.
            reverse: Whether to sort in reverse order.
        """
        if field not in _STOCK_ROW_FIELD_BY_HEADER_IDX:
            raise ValueError(f"Invalid field for sorting: {field}")

        match field:
            case "uncertainty":
                def by_uncertainty(row: StockRow) -> int:
                    if row.uncertainty is None:
                        return -1
                    return _UNCERTAINTIES_ORDER.index(row.uncertainty)
                key_func = by_uncertainty
            case "action":
                def by_action(row: StockRow) -> int:
                    if row.action not in _ACTIONS_ORDER:
                        return -1
                    return _ACTIONS_ORDER.index(row.action)
                key_func = by_action
            case "lastCachedDate" | "fairValueDate":
                def by_date(row: StockRow) -> datetime:
                    date_str = getattr(row, field, None)
                    if date_str is None:
                        return datetime.min
                    return datetime.fromisoformat(date_str)
                key_func = by_date
            case _:
                def by_default(row: StockRow) -> Any:
                    value = getattr(row, field, None)
                    if value is None:
                        return -1
                    return value
                key_func = by_default

        self.raw_data.sort(key=key_func, reverse=reverse)
        self.rows = [_fmt_stock_row(data) for data in self.raw_data]


def _sign(x: float) -> str:
    return "+" if x >= 0 else "-"


def _fmt_price(x: float, with_sign: bool = False) -> str:
    return f"{_sign(x)}${abs(x):.2f}" if with_sign else f"${x:.2f}"


def _fmt_pct(x: float, with_sign: bool = False) -> str:
    return f"{_sign(x)}{abs(x):.2f}%" if with_sign else f"{x:.2f}%"


def _fmt_date(x: str) -> str:
    try:
        return datetime.fromisoformat(x).strftime("%m/%d/%y")
    except ValueError:
        return _NOT_AVAILABLE


def _fmt_stock_row(data: StockRow) -> list[str]:

    def _fmt_company_name(name: str) -> str:
        """Shorten company name to 2 words and fit within 12 characters."""
        return " ".join(name.split(" ")[:2])[:12]

    def _fmt_star_rating(stars: int | None) -> str:
        """Format star rating to fit within 5 characters."""
        return "★" * stars if stars else "N/A"

    return [
        _fmt_company_name(data.name),
        data.ticker,
        _fmt_pct(data.dayChangePer, with_sign=True),
        _fmt_price(data.lastPrice),
        _fmt_price(data.latestFairValue)
        if data.latestFairValue is not None else _NOT_AVAILABLE,
        _fmt_pct(data.discount * 100)
        if data.discount is not None else _NOT_AVAILABLE,
        _fmt_star_rating(data.starRating),
        data.uncertainty or _NOT_AVAILABLE,
        data.action,
        _fmt_date(data.fairValueDate)
        if data.fairValueDate is not None else _NOT_AVAILABLE,
        _fmt_date(data.lastCachedDate),
    ]


def _fmt_row_data(rows: list[list[str]], column_widths: list[int]) -> list[str]:
    """Format the row data for display in the MultiLine widget."""
    return [
        "".join(
            str(item).center(column_widths[idx], " ")
            for idx, item in enumerate(row)
        ) for row in rows
    ]


def _fetch_raw_table_data(symbols: Iterable[str]) -> list[StockRow]:
    """
    Fetch stock data for the given symbols and format it for display.
    Returns a list of rows, each row is a list of strings.
    """
    fetch_data = functools.partial(
        StockAPI().get_info, cache_option=CacheOption.CHECK_ALL)

    rows: list[StockRow] = []
    for symbol in symbols:
        if (stock_info := fetch_data(symbol)) is None:
            raise ValueError(f"Failed to fetch data for symbol: {symbol}")

        rows.append(StockRow.model_construct(
            **stock_info.model_dump(),
            action=alerting.get_action(stock_info)
        ))
    return rows


def _calculate_column_widths(table: ScreenerTable, padding: int = 2) -> list[int]:
    """
    Calculate the width of each column based on the headers and rows.
    Returns a list of widths for each column.
    """
    widths = [len(header) + padding for header in table.headers]
    if not table.rows:
        return widths

    for col in range(len(table.rows[0])):
        max_item_length_in_col = max(len(row[col]) for row in table.rows)
        widths[col] = max(widths[col], max_item_length_in_col + padding)

    return widths


class StockScreener(npyscreen.FormBaseNew):
    """Stock Screener Table screen for displaying stock data."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Hold sorting state
        self.sort_field = ""
        self.sort_reverse = False

    def create(self):
        if self.columns is None or self.lines is None:
            raise ValueError("Form must have defined dimensions.")

        self.symbols = self.parentApp.symbols
        if not self.symbols or not isinstance(self.symbols, Iterable):
            raise ValueError("No symbols provided for Stock Screener.")

        screen.add_close_button(self)
        screen.add_back_button(self, self._go_home)
        self.add(npyscreen.TitleText, name="Stock Screener", editable=False)
        self.add(
            npyscreen.FixedText,
            value=f"Sort by [0-{len(_COLUMN_NAMES[:9])}]",
            editable=False,
            rely=4,
            relx=3,
            color=_INSTRUCTION_COLOR,
        )

        self.sort_field = ""
        self.sort_reverse = False

        # Create table data
        raw_table_data = _fetch_raw_table_data(self.symbols)
        self.table = ScreenerTable(
            headers=[f"[{idx}]{col}"
                     if idx < 10 else col
                     for idx, col in enumerate(_COLUMN_NAMES)],
            rows=[_fmt_stock_row(data) for data in raw_table_data],
            raw_data=raw_table_data
        )

        # Create header widgets
        self.column_padding = 2  # Add padding to account for ▲/▼ chars and spacing
        self.column_widths = _calculate_column_widths(
            self.table,
            self.column_padding,
        )
        self.header_widgets = []
        header_start_x = 3
        for width in self.column_widths:
            self.header_widgets.append(self.add(
                npyscreen.FixedText,
                rely=6,
                relx=header_start_x,
                editable=False,
                color=_HEADER_COLOR,
            ))
            header_start_x += width
        self._refresh_headers()

        # Create table data widget
        self.screen_data = self.add(
            npyscreen.MultiLine,
            name="Stocks",
            values=_fmt_row_data(self.table.rows, self.column_widths)
            if self.table.rows else ["No data available"],
            relx=2,
            max_height=self.lines - 10,
            max_width=self.columns - 6,
            scroll_exit=True,
            editable=bool(self.table.rows),
            color="STANDOUT",
            highlight_color="GOOD",
        )

        screen.add_ctrl_c_handler(self)
        self.screen_data.add_handlers({
            ord("q"): lambda _: self._go_home(),
            ord("h"): lambda _: self._go_home(),
            27: lambda _: self._go_home(),  # Escape key
        })

    def handle_input(self, key):
        sort_keys = {
            ord(str(i)): i for i in range(len(self.table.headers[:10]))
        }
        if key in sort_keys:
            col_idx = sort_keys[key]
            field = _STOCK_ROW_FIELD_BY_HEADER_IDX[col_idx]
            self._sort_by_field(field)
        else:
            super().handle_input(key)

    def _go_home(self):
        self.parentApp.switchForm(screen.Screen.MAIN)

    def _sort_by_field(self, field: str):
        """Sort the table by the specified field."""
        if field == self.sort_field:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_field = field
            self.sort_reverse = False

        self.table.sort_by(field, reverse=self.sort_reverse)

        if not isinstance(self.screen_data, npyscreen.MultiLine):
            raise TypeError("expected screen_data to be a MultiLine widget")

        self.screen_data.values = _fmt_row_data(
            self.table.rows, self.column_widths
        )
        self.screen_data.display()
        self._refresh_headers()

    def _refresh_headers(self):
        """Render the header row with clickable sorting."""
        for idx, header_widget in enumerate(self.header_widgets):
            header = self.table.headers[idx]
            field = _STOCK_ROW_FIELD_BY_HEADER_IDX[idx]
            if self.sort_field == field:
                arrow = "▲" if not self.sort_reverse else "▼"
                rendered_header = f"{header}{arrow}"
                header_widget.color = _ACTIVE_COLOR
            else:
                rendered_header = header
                header_widget.color = _HEADER_COLOR

            header_widget.value = rendered_header.center(
                self.column_widths[idx] - self.column_padding, " ")
            header_widget.display()
