import npyscreen
import src.common as common


class HomeScreen(npyscreen.FormBaseNew):
    def create(self):
        common.add_close_button(self)
        self.add(npyscreen.TitleText, name="Market View", editable=False)
        self.add(npyscreen.ButtonPress, name="Stock Screen", when_pressed_function=self._enter_stock_screen)

    def _enter_stock_screen(self):
        self.parentApp.switchForm(common.Screen.STOCK_SCREENER.name)
