import random
from datetime import date
from decimal import Decimal

from py_portfolio_index.enums import ProviderType
from py_portfolio_index.models import (
    Money,
    RealPortfolio,
    RealPortfolioElement,
)

from .base_portfolio import BaseProvider

DEFAULT_CASH = 10000


class FixedGen:
    def __init__(self, value: float):
        self.value = value

    def get(self):
        return self.value


class RandGen:
    def __init__(self, seed: int | None = None):
        random.seed(seed)

    def get(self):
        return random.randint(150, 10000) / 100


class LocalDictProvider(BaseProvider):
    PROVIDER = ProviderType.LOCAL_DICT

    def __init__(
        self,
        holdings: list[RealPortfolioElement],
        price_dict: dict[str, Decimal] | None = None,
        default_price_gen=RandGen,
        cash: Money | None = None,
    ):
        BaseProvider.__init__(self)
        self._price_dict = price_dict or {}
        self._portfolio = RealPortfolio(holdings=holdings, provider=self, cash=cash if cash is not None else Money(value=DEFAULT_CASH))
        self.default_price_gen = default_price_gen()

    @property
    def cash(self) -> Money:
        return self._portfolio.cash or Money(value=0)

    def _get_instrument_price(self, ticker: str, at_day: date | None = None, fail_on_missing: bool = True) -> Decimal:
        value = self._price_dict.get(ticker)
        if not value:
            nvalue = self.default_price_gen.get()
            self._price_dict[ticker] = nvalue
            return nvalue
        return value

    def _get_instrument_prices(
        self,
        tickers: list[str],
        at_day: date | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal]:
        for ticker in tickers:
            value = self._price_dict.get(ticker)
            if not value:
                nvalue = self.default_price_gen.get()
                self._price_dict[ticker] = nvalue
        return {ticker: self._price_dict[ticker] for ticker in tickers}

    def buy_instrument(self, ticker: str, qty: Decimal, value: Money | None = None):
        price = self.get_instrument_price(ticker)
        if not price:
            raise ValueError("No available price for this instrument")
        if value:
            qty = value / price
            value_delta = value
        else:
            value_delta = Money(value=qty * price)
        self._portfolio += RealPortfolioElement(ticker=ticker, units=qty, value=value_delta)

    def get_unsettled_instruments(self) -> set[str]:
        # we settle right away
        return set()

    def get_holdings(self):
        return self._portfolio


class LocalDictNoPartialProvider(LocalDictProvider):
    PROVIDER = ProviderType.LOCAL_DICT_NO_PARTIAL
    SUPPORTS_FRACTIONAL_SHARES = False
