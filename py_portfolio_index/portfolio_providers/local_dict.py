import random
from typing import List, Dict, Optional, Set
from datetime import date
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.models import Money
from py_portfolio_index.enums import Provider
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
    PROVIDER = Provider.LOCAL_DICT

    def __init__(
        self,
        holdings: List[RealPortfolioElement],
        price_dict: Optional[Dict[str, Decimal]] = None,
        default_price_gen=RandGen,
        cash: Money = Money(value=10000),
    ):
        BaseProvider.__init__(self)
        self._price_dict = price_dict or {}
        self._portfolio = RealPortfolio(holdings=holdings, provider=self, cash=cash)
        self.default_price_gen = default_price_gen()

    def _get_instrument_price(self, ticker: str, at_day: Optional[date] = None):
        value = self._price_dict.get(ticker)
        if not value:
            nvalue = self.default_price_gen.get()
            self._price_dict[ticker] = nvalue
            return nvalue
        return value

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        price = self.get_instrument_price(ticker)
        if not price:
            raise ValueError("No available price for this instrument")
        if value:
            qty = value / price
            value_delta = value
        else:
            value_delta = Money(value=qty * price)
        self._portfolio += RealPortfolioElement(
            ticker=ticker, units=qty, value=value_delta
        )

    def get_unsettled_instruments(self) -> Set[str]:
        # we settle right away
        return set()

    def get_holdings(self):
        return self._portfolio
