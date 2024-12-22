from enum import IntEnum, Enum


class ProviderType(str, Enum):
    ALPACA = "alpaca"
    ALPACA_PAPER = "alpaca_paper"
    ROBINHOOD = "robinhood"
    LOCAL_DICT = "local_dict"
    LOCAL_DICT_NO_PARTIAL = "local_dict_no_partial"
    DUMMY = "dummy"
    WEBULL = "webull"
    WEBULL_PAPER = "webull_paper"
    MOOMOO = "moomoo"
    SCHWAB = "schwab"


class OrderType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ObjectKey(Enum):
    POSITIONS = 0
    DIVIDENDS = 1
    UNSETTLED = 2
    ACCOUNT = 3
    OPEN_ORDERS = 4
    MISC = 5
    DIVIDENDS_DETAIL = 6


class ProviderClass(Enum):
    PAPER = [
        ProviderType.ALPACA_PAPER,
        ProviderType.WEBULL_PAPER,
        ProviderType.LOCAL_DICT,
    ]
    REAL = [
        ProviderType.ALPACA,
        ProviderType.WEBULL,
        ProviderType.ROBINHOOD,
        ProviderType.SCHWAB,
    ]


class PurchaseStrategy(IntEnum):
    CHEAPEST_FIRST = 1
    LARGEST_DIFF_FIRST = 2
    PEANUT_BUTTER = 3


class RoundingStrategy(int, Enum):
    CLOSEST = 1
    FLOOR = 2
    CEILING = 3


class Currency(str, Enum):
    USD = "$"
    EURO = "€"
    POUND = "£"
