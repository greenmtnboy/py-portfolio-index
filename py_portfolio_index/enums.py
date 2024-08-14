from enum import IntEnum, Enum


class Provider(str, Enum):
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


class ProviderClass(Enum):
    PAPER = [Provider.ALPACA_PAPER, Provider.WEBULL_PAPER, Provider.LOCAL_DICT]
    REAL = [Provider.ALPACA, Provider.WEBULL, Provider.ROBINHOOD, Provider.SCHWAB]


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
