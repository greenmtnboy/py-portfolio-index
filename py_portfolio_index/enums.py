from enum import IntEnum, Enum


class PurchaseStrategy(IntEnum):
    CHEAPEST_FIRST = 1
    LARGEST_DIFF_FIRST = 2


class RoundingStrategy:
    CLOSEST = 1
    FLOOR = 2
    CEILING = 3


class Currency(Enum):
    USD = "$"
    EURO = "€"
    POUND = "£"
