from decimal import Decimal
from typing import Union, Any
from py_portfolio_index.models import Money
from math import ceil, pow


def print_per(input: Union[Money, Decimal]):
    if isinstance(input, Money):
        return f"{round(input.value*100,4)}%"
    return f"{round(input*100,4)}%"


def print_money(input: Union[Money, Decimal]):
    if isinstance(input, Money):
        return f"{round(input,2)}"
    return f"${round(input,2)}"


def round_up_to_place(input: Decimal, places: int = 2) -> Decimal:
    factor = Decimal(pow(Decimal(10.0), places))
    return ceil(input * factor) / Decimal(factor)


def divide_into_batches(lst: list, batch_size: int = 50) -> list[list[Any]]:
    """
    Divide a list into batches of a specified size.

    Args:
        lst: The list to be divided.
        batch_size: The size of each batch.

    Returns:
        A list of batches, where each batch is a sublist of the original list.
    """
    batches = []
    for i in range(0, len(lst), batch_size):
        batch = lst[i : i + batch_size]
        batches.append(batch)
    return batches
