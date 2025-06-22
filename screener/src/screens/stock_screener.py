import datetime

import npyscreen

from src import common
from src import morningstar as ms


_NOT_AVAILABLE = "N/A"
_UNCERTAINTIES_ORDER = ["Very Low", "Low", "Medium", "High", "Very High"]
_DATE_FORMAT = "%m/%d/%y"

class StockScreener(npyscreen.FormBaseNew):
    COLUMN_NAMES = [
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
    INSTRUCTION_COLOR = "STANDOUT"
    HEADER_COLOR   = "CONTROL"
    ACTIVE_COLOR   = "GOOD"
    
    def create(self):
        self.ms_api = ms.MoringstarAPI.get_instance()
        self.add(npyscreen.TitleText, name="Stock Screener", editable=False)
        self.add(
            npyscreen.FixedText, 
            value=f"Sort by [0-{len(self.COLUMN_NAMES[:9])}]",
            editable=False,
            rely=4,
            relx=3,
            color=StockScreener.INSTRUCTION_COLOR,
        )

        self.add(
            npyscreen.ButtonPress,
            name="Back",
            when_pressed_function=self._go_to_home,
            relx=self.columns - 15,
            rely=1,
        )

        self.stock_rows = self._fetch_screener_data()

        # Header Text
        self.sort_column = None
        self.sort_reverse = False
        self.header_names = [f"[{idx}]{col}" if idx < 10 else col for idx, col in enumerate(self.COLUMN_NAMES)]
        header_lengths = [len(c) for c in self.header_names]
        max_item_lengths = [max(len(str(row[idx])) for row in self.stock_rows) for idx in range(len(self.header_names))]
        # Add padding, accounting for ▲/▼ chars and spacing
        self.col_padding = 2
        self.column_widths = [
            max(header_lengths[idx], max_item_lengths[idx]) + self.col_padding 
            for idx in range(len(self.header_names))
        ]
        header_start_x = 3
        self.header_widgets = []
        for idx, (header, col_len) in enumerate(zip(self.header_names, self.column_widths)):
            centered_header = header.center(col_len - self.col_padding, " ")
            header_widget = self.add(
                npyscreen.FixedText,
                value=centered_header, 
                name=centered_header,
                when_pressed_function=lambda idx=idx: self._sort_by_column(idx),
                rely=6,
                relx=header_start_x,
                editable=False,
                color=StockScreener.HEADER_COLOR,
            )
            header_start_x += col_len
            self.header_widgets.append(header_widget)

        self.screen_data = self.add(
            npyscreen.MultiLine,
            name="Stocks",
            values=(
                self._format_row_data(self.stock_rows) 
                if self.stock_rows else ["No data available"]
            ),
            max_height=20,
            # width=150,
            relx=2,
            scroll_exit=True,
            editable=bool(self.stock_rows),
            color="STANDOUT",         # <— makes the whole widget purple/magenta
            highlight_color="GOOD",   # <— when you move focus into it, it turns green
        )

    def handle_input(self, key):
        sort_keys = {ord(str(i)): i for i in range(len(self.header_names[:10]))}
        if key in sort_keys:
            col_idx = sort_keys[key]
            self._sort_by_column(col_idx)
        else:
            super().handle_input(key)

    def _sort_by_column(self, col_idx) -> None:
        if self.sort_column == col_idx:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col_idx
            self.sort_reverse = False

        def clean(item) -> str:
            for token in ("$", "%", "."):
                item = item.replace(token, "")
            item = item.strip()
            try:
                # "+1.2".isnumeric() is False, but float("+1.2") is valid
                return float(item)
            except ValueError:
                pass

            try:
                return datetime.datetime.strptime(item, _DATE_FORMAT)
            except ValueError:
                pass

            if item in _UNCERTAINTIES_ORDER:
                return _UNCERTAINTIES_ORDER.index(item)
            return item

        sorted_data = sorted(
            self.stock_rows,
            key=lambda row: (
                # force tuple sorting to handle N/A values
                row[col_idx] is not _NOT_AVAILABLE, clean(str(row[col_idx]))
            ),
            reverse=self.sort_reverse
        )
        self.screen_data.values = self._format_row_data(sorted_data)
        self.screen_data.display()
        self._render_headers()

    def _render_headers(self):
        for idx, widget in enumerate(self.header_widgets):
            base = self.header_names[idx]
            if idx == self.sort_column:
                arrow = "▼" if self.sort_reverse else "▲"
                label = f"{base}{arrow}"
                widget.color = StockScreener.ACTIVE_COLOR
            else:
                label = base
                widget.color = StockScreener.HEADER_COLOR

            widget.value = label.center(self.column_widths[idx] - self.col_padding, " ")
            widget.display()

    def _go_to_home(self) -> None:
        self.parentApp.switchForm(common.Screen.MAIN.name)

    def _fetch_screener_data(self) -> list[list[str]]:
        stock_data_rows = []
        for ticker in common.STOCKS:
            data = self.ms_api.get_stock_data(ticker)
            if data is None:
                print(f"Failed to fetch data for {ticker}")
            else:
                stock_data_rows.append(data)

        def format_row(stock_data):
            format_name = lambda x: " ".join(x.split(" ")[:2])[:12]
            format_percent = lambda x: f"{'+' if float(x) > 0 else '-'}{abs(x):.2f}%" if x else "N/A"
            format_cash = lambda x: f"${float(x):.2f}" if x else "N/A"
            format_decimal = lambda x: f"{float(x):.2f}" if x else "N/A"
            format_rating = lambda x: "★" * int(x) if x else "N/A"
            format_uncertainty = lambda x: x.strip() if x else "N/A"
            format_time = lambda x: datetime.datetime.fromisoformat(x).strftime(_DATE_FORMAT) if x else "N/A"
            def action(s):
                if s["discount"] is None:
                    return "N/A"
                discount = float(s["discount"])
                uncertainty = s["uncertainty"]
                discount_factor = 0.06*(1+_UNCERTAINTIES_ORDER.index(uncertainty))
                if discount <= 1-discount_factor:
                    return "BUY"
                elif discount >= 1+discount_factor:
                    return "SELL"
                return "HOLD"

            return [
                format_name(stock_data["name"]),
                stock_data["ticker"],
                format_percent(stock_data["dayChangePer"]),
                format_cash(stock_data["lastPrice"]),
                format_cash(stock_data["latestFairValue"]),
                format_decimal(stock_data["discount"]),
                format_rating(stock_data["starRating"]),
                format_uncertainty(stock_data["uncertainty"]),
                action(stock_data),
                format_time(stock_data["fairValueDate"]),
                format_time(stock_data["lastCachedDate"]),
            ]

        return [format_row(data) for data in stock_data_rows]

    def _format_row_data(self, data) -> list[str]:
        return [
            "".join(
                str(item).center(self.column_widths[idx])
                for idx, item in enumerate(row)
            )
            for row in data
        ]
