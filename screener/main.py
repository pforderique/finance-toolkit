import npyscreen
import dotenv

from src.screens.home import HomeScreen
from src.screens.stock_screener import StockScreener
from src.cache import Cache
import src.common as common


class PortfolioApp(npyscreen.NPSAppManaged):
    def onStart(self):
        self.addForm(common.Screen.MAIN.name, HomeScreen)
        self.addForm(common.Screen.STOCK_SCREENER.name, StockScreener)


def check_all_stocks_loaded():
    """Check if all stocks are loaded in the cache."""
    c = Cache.load()
    missing_stocks = [
        f'"{stock}"' for stock in common.STOCKS
        if not c.get("/market/v3/auto-complete", stock)
    ]
    if missing_stocks:
        print(f"Missing stocks in cache: {', '.join(missing_stocks)}")
        return False
    return True

if __name__ == "__main__":
    dotenv.load_dotenv()
    if not check_all_stocks_loaded():
        exit(1)        
    app = PortfolioApp()
    app.run()
