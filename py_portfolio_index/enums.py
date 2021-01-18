from enum import IntEnum, Enum


class BuyOrder(IntEnum):
    CHEAPEST_FIRST = 1
    LARGEST_DIFF_FIRST = 2


class Currency(Enum):
    USD = "$"
    EURO = "€"
    POUND = "£"
