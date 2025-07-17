"""Home Screen for the Stock Screener Application."""

import npyscreen

from screener.ui import screen


class HomeScreen(npyscreen.FormBaseNew):
    """Home screen for the Stock Screener application."""

    def create(self):
        screen.add_close_button(self)
        screen.add_ctrl_c_handler(self)
        self.add(npyscreen.TitleText, name="Market View", editable=False)
        self.add(
            npyscreen.ButtonPress,
            name="Stock Screener",
            when_pressed_function=self._enter_screener
        )

    def _enter_screener(self):
        self.parentApp.switchForm(screen.Screen.STOCK_SCREENER)
