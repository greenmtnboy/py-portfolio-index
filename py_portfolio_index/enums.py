from enum import IntEnum, Enum

class Provider(str, Enum):
    ALPACA = "alpaca"
    ROBINHOOD = "robinhood"

class PurchaseStrategy(IntEnum):
    CHEAPEST_FIRST = 1
    LARGEST_DIFF_FIRST = 2


class RoundingStrategy(int, Enum):
    CLOSEST = 1
    FLOOR = 2
    CEILING = 3


class Currency(str, Enum):
    USD = "$"
    EURO = "€"
    POUND = "£"
