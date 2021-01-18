import random
from typing import List, Dict, Optional

from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    IdealPortfolio,
)
from .base_portfolio import BaseProvider


class FixedGen:
    def __init__(self, value: float):
        self.value = value

    def get(self):
        return self.value


class RandGen:
    def __init__(self, seed: Optional = None):
        random.seed(seed)

    def get(self):
        return random.randint(150, 10000) / 100


class LocalDictProvider(BaseProvider):
    def __init__(
        self,
        holdings: List[RealPortfolioElement],
        price_dict: Optional[Dict[str, float]] = None,
        default_price_gen=RandGen,
    ):
        BaseProvider.__init__(self)
        self._price_dict = price_dict or {}
        self._portfolio = RealPortfolio(holdings=holdings)
        self.default_price_gen = default_price_gen()

    def get_instrument_price(self, ticker: str):
        value = self._price_dict.get(ticker)
        if not value:
            value = self.default_price_gen.get()
            self._price_dict[ticker] = value
        return value

    def buy_instrument(self, ticker: str, qty: float):
        self._portfolio += RealPortfolioElement(
            ticker=ticker, units=qty, value=qty * self.get_instrument_price(ticker)
        )

    def get_holdings(self):
        return self._portfolio
