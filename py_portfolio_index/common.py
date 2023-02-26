from decimal import Decimal
from typing import Union
from py_portfolio_index.models import Money


def print_per(input: Union[Money, Decimal]):
    if isinstance(input, Money):
        return f"{round(input.value*100,4)}%"
    return f"{round(input*100,4)}%"


def print_money(input: Union[Money, Decimal]):
    if isinstance(input, Money):
        return f"{round(input,2)}"
    return f"${round(input,2)}"
