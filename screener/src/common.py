import npyscreen
import enum


class Screen(enum.Enum):
    MAIN = "MAIN"
    STOCK_SCREENER = "STOCK_SCREENER"


def add_close_button(form: npyscreen.FormBaseNew):
    form.add(
        npyscreen.ButtonPress,
        name="X",
        when_pressed_function=lambda: form.parentApp.switchForm(None),
        relx=form.columns - 8,
        rely=1,
        color="DANGER",
    )

STOCKS = [
    # ETFS
    # "VOO", "QQQM", "VTI", "VOOG", "SPY", "SCHD", "JEPI", "ETHUSD", "SOXX", "DXJ",
    # "QQQ", "ISCG",

    # All Stocks
    # "NVDA", "GOOG", "MSFT", "LLY", "UNH", "BLK", "AAPL", "COST", "META",
    # "TSLA", "GOOGL", "COKE", "O", "CMG", "BRK.B", "NFLX", "NOC", "LCID",
    # "SPGI", "MCK", "V", "TSCO", "CAVA", "EPD", "DELL", "PANW",
    # "UAL", "CRWD", "WM", "BA","HSY", "DAL", "BX",
    # "FSLR", "AMAT", "AMZN", "MCD", "WMT", "AVGO", "KO", "AXP", "ADI",
    # "AMD", "TGT", "HOOD", "UBER", "COR", "IQV",
    # "DIS", "MU", "RIVN", "QCOM", "NLY", "ZTS", "CRBP", "IONQ", "LCID",
    # "BKNG", "LNW", 

    # Pre-Selected (different per line)
    # "FSLR", "TSLA", "RIVN", "AAPL", "CMG", "TGT", "EPD", "HOOD", "AMZN", "MU", "AVGO", "DAL", "UAL", "AMD", "CRWD", "COKE", "UBER", "UNH", "PANW",
    # "ZTS", "MCK", "CAVA", "HOOD", "AMD", "LLY", "NVDA", "TSLA", "MU", "CRWD", "BX", "AVGO", "AMAT", "ADI", "NLY", "DELL", "AXP", "UNH", "BLK", "SPGI", "VRTX", "V",
    # "DAL", "ADI", "FSLR", "LLY", "AAPL", "TSLA", "META", "AMAT", "AVGO", "BX", "CAVA", "AMZN", "AMD", "RIVN", "UAL", "NVDA", "QCOM", "NKE", "UBER", "TSM", "MU", "HOOD", "DAL", "DELL", "TGT", "KO", "O", "UNH", "EPD", "SPGI"
    # "HOOD", "FSLR", "UNH", "AXP", "DHR", 
    # "AVGO", "MU","DELL", "AMD", "UAL", "CRWD", "META", "UBER", "TSM", "AMAT", "BA", "AMZN", "CAVA", "IQV", "PFE"
    # "IQV", "DHR", "PFE", "ZTS", "LLY", "O", "AMD", "GOOG", "DELL", "NVDA", "CMG", "TSLA", "BA", "WMT", "BRK.B", "MSFT",
    # "KO", "MCD", "RIVN", "ZTS", "COKE", "PFE", "V", "COST", "PANW", "BA", "EPD", "TGT", "BLK", "O", "SPGI", "BX", "META", "MU", "IQV", "HOOD", "FSLR", "UNH" 
    # "AMAT", "LCID", "CAVA", "FSLR", "META", "AVGO", "ZTS", "TGT", "CRWD", "NKE", "WMT", "MU", "AMD", "DELL", "UBER", "LLY"
    # "UNH", "HOOD", "UAL", "CRWD", "MCD", "TSM", "COKE", "AAPL", "AMD", "TSLA", "CAVA", "FSLR"
    # "RIVN", "FSLR", "PFE", "NKE", "BA", "UNH", "TSLA", "AKAM", "TSCO", "UAL", "NVDA", "CRWD", "AMZN", "AXP", "TSM", "MSFT"
    # "PANW", "CAVA", "TGT", "UNH", "IQV", "DHR", "UBER", "TSCO", "COKE", "BX", "BA", "NLY", "MCD", "AVGO", "NVDA", "AMD", "HOOD",
    # "FSLR", "ADI", "RIVN", "QCOM", "TSCO", "LLY", "NLY", "KO", "TSLA", "CRWD", "PANW", "GOOG",
    # "AMAT", "TSM", "HOOD", "AAPL", "MU", "IQV", "CRWD", "FSLR"
    # "TGT", "IQV", "NKE", "LLY", "TSLA", "TSM", "GOOGL", "FSLR", "GOOG", "MU", "ADI", "QCOM", "DIS", "TSCO", "HOOD", "BA", "UBER", "MCD", "AVGO", "AMAT", "AAPL", 
    # "FSLR", "DHR", "MCD", "COKE", "LLY", "EPD", "UNH", "AMD", "UAL", "CMG", "HOOD", "MU", "DAL", "ADI", "AKAM", "DELL", "AMAT", "META", "AXP", "TSM", "RIVN", "KO", 
    # "NKE", "BA", "BX", "AXP", "TGT", "UNH", "TSM", "V", "WMT", "BLK", "CMG", "UBER", "RIVN", "FSLR", "DELL", "LLY", "MSFT", "SPGI",
    #  "NVDA", "HOOD", "FSLR", "CAVA", "AVGO", "CMG", "CRWD", "V", "TSCO", "BA", "AMZN", "O", "DAL", "DHR"
    # 'HOOD', 'FSLR', 'CAVA', 'AVGO', 'CMG', 'CRWD'
    "FSLR", "CAVA", "PANW", "AVGO", "CRWD", "BA", "MSFT", "COKE", "DELL", "NVDA", "BLK", "HOOD", "TGT", 
]
