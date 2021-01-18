from dataclasses import dataclass
from py_portfolio_index.enums import Currency


@dataclass
class Config:
    default_currency = Currency
