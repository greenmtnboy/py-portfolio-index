from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Union
from pydantic import BaseModel
from pandas import DataFrame
from py_portfolio_index.enums import Currency
from py_portfolio_index.constants import Logger


@dataclass
class Money:
    value: float
    currency: Currency

    @classmethod
    def parse(cls, val):
        from py_portfolio_index.config import Config

        currency = Config.default_currency
        if isinstance(val, Money):
            return Money
        elif isinstance(val, (float, int)):
            return Money(float(val), currency=currency)
        elif isinstance(val, str):

            for c in Currency:
                if c.name in val:
                    val = val.replace(c.name, "")
                    currency = c
            return Money(float(val), currency=currency)

    def _cmp_helper(self, other):
        if isinstance(other, Money):
            if other.currency != self.currency:
                raise ValueError("Currency conversions not supported")
            return other.value
        return other

    def __eq__(self, other):
        return self.value == self._cmp_helper(other)

    def __ne__(self, other):
        return self.value != self._cmp_helper(other)

    def __gt__(self, other):
        return self.value > self._cmp_helper(other)

    def __ge__(self, other):
        return self.value >= self._cmp_helper(other)

    def __lt__(self, other):
        return self.value < self._cmp_helper(other)

    def __le__(self, other):
        return self.value <= self._cmp_helper(other)

    def __add__(self, other):
        return Money(self.value + self._cmp_helper(other), currency=self.currency)

    def __sub__(self, other):
        return Money(self.value - self._cmp_helper(other), currency=self.currency)

    def __mul__(self, other):
        return Money(self.value * self._cmp_helper(other), currency=self.currency)

    def __truediv__(self, other):
        return Money(self.value / self._cmp_helper(other), currency=self.currency)

    def __float__(self):
        return self.value

    def __int__(self):
        return int(self.value)


class IdealPortfolioElement(BaseModel):
    ticker: str
    weight: float


class IdealPortfolio(BaseModel):
    holdings: List[IdealPortfolioElement]

    def _reweight_portfolio(self):
        weights = sum([item.weight for item in self.holdings])

        scaling_factor = 1 / weights

        for item in self.holdings:
            item.weight = item.weight * scaling_factor

    def exclude(self, exclusion_list: List[str]):
        reweighted = []
        excluded = 0
        for ticker in exclusion_list:
            for item in self.holdings:
                if item.ticker == ticker:
                    excluded += item.weight
                    item.weight = 0.0
                    reweighted.append(item.ticker)
        self.holdings = [
            item for item in self.holdings if item.ticker not in [reweighted]
        ]
        self._reweight_portfolio()
        Logger.info(
            f"Set the following stocks to weight 0 {reweighted}. Total value excluded {excluded}."
        )
        return self

    def reweight(
        self, ticker_list: List[str], weight: float, min_weight: float = 0.005
    ):
        reweighted = []
        total_value = 0
        for ticker in ticker_list:
            found = False
            for item in self.holdings:
                if item.ticker == ticker:
                    total_value += item.weight * weight
                    item.weight = item.weight * weight
                    reweighted.append(ticker)
                    found = True
            if not found:
                reweighted.append(ticker)
                total_value += min_weight
                self.holdings.append(
                    IdealPortfolioElement(ticker=ticker, weight=min_weight)
                )
        self._reweight_portfolio()
        Logger.info(
            f"modified the following by weight {weight} {reweighted}. Total value modified {total_value}."
        )
        return self

    def produce_tear_sheet_from_date(self, datetime: datetime):
        raise NotImplementedError
        import pyfolio

        columns = ["date"] + [item.ticker for item in self.holdings]
        values = [datetime] + [item.value for item in self.holdings]
        df = DataFrame([values], columns=columns)
        df.set_index(keys=["date"], drop=True)
        pyfolio.create_simple_tear_sheet(positions=df, live_start_date=datetime)


class RealPortfolioElement(IdealPortfolioElement):
    ticker: str
    units: float
    value: Union[float, int, Money]
    weight: Optional[float] = None

    def __post_init__(self):
        self.value = Money.parse(self.value)


class RealPortfolio(IdealPortfolio):
    holdings: List[RealPortfolioElement]

    @property
    def _index(self):
        return {val.ticker: val for val in self.holdings}

    def get_holding(self, ticker: str):
        return self._index.get(ticker)

    @property
    def value(self) -> float:
        return sum([item.value for item in self.holdings])

    def _reweight_portfolio(self):
        value = self.value
        for item in self.holdings:
            item.weight = float(item.value / value)

    def add_holding(self, holding: RealPortfolioElement):
        existing = self._index.get(holding.ticker)
        if existing:
            existing.value += holding.value
            existing.units += holding.units
        if not existing:
            self.holdings.append(holding)
        self._reweight_portfolio()

    def __add__(self, other):
        if isinstance(other, RealPortfolioElement):
            self.add_holding(other)
        elif isinstance(other, RealPortfolio):
            for item in other.holdings:
                self.add_holding(item)
        else:
            raise ValueError
        return self
