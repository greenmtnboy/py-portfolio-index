from decimal import Decimal


def print_per(input: Decimal):
    return f"{round(input*100,4)}%"


def print_money(input: Decimal):
    return f"${round(input,2)}"
