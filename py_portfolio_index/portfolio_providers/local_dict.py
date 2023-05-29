import random
from typing import List, Dict, Optional
from datetime import date
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.models import Money
from decimal import Decimal
from .base_portfolio import BaseProvider


class FixedGen:
    def __init__(self, value: float):
        self.value = value

    def get(self):
        return self.value


class RandGen:
    def __init__(self, seed: Optional[int] = None):
        random.seed(seed)

    def get(self):
        return random.randint(150, 10000) / 100


class LocalDictProvider(BaseProvider):
    def __init__(
        self,
        holdings: List[RealPortfolioElement],
        price_dict: Optional[Dict[str, Decimal]] = None,
        default_price_gen=RandGen,
    ):
        BaseProvider.__init__(self)
        self._price_dict = price_dict or {}
        self._portfolio = RealPortfolio(holdings=holdings)
        self.default_price_gen = default_price_gen()

    def _get_instrument_price(self, ticker: str, at_day: Optional[date] = None):
        value = self._price_dict.get(ticker)
        if not value:
            nvalue = self.default_price_gen.get()
            self._price_dict[ticker] = nvalue
            return nvalue
        return value

    def buy_instrument(self, ticker: str, qty: Decimal):
        price = self.get_instrument_price(ticker)
        if not price:
            raise ValueError("No available price for this instrument")
        self._portfolio += RealPortfolioElement(
            ticker=ticker, units=qty, value=Money(value=qty * price)
        )

    def get_holdings(self):
        return self._portfolio
