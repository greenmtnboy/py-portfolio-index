from datetime import datetime, date
from typing import List, Optional, Union, TYPE_CHECKING
from pydantic import BaseModel, Field, validator
from pandas import DataFrame
from py_portfolio_index.enums import Currency
from py_portfolio_index.constants import Logger
from py_portfolio_index.exceptions import PriceFetchError
from decimal import Decimal

if TYPE_CHECKING:
    from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider


class Money(BaseModel):
    value: Union[Decimal, int, float, "Money"]
    currency: Currency = Currency.USD

    @validator("value", pre=True)
    def coerce_to_decimal(cls, v):
        if isinstance(v, int):
            return Decimal(v)
        if isinstance(v, Money):
            # TODO convert this
            return Decimal(v.value)
        return v

    def __str__(self):
        return f"{self.currency.value}{self.value}"

    def __repr__(self):
        return str(self)

    @classmethod
    def parse(cls, val) -> "Money":
        from py_portfolio_index.config import Config

        currency = Config.default_currency
        if isinstance(val, Money):
            return val
        elif isinstance(val, (Decimal, float, int)):
            return Money(value=Decimal(val), currency=currency)
        elif isinstance(val, str):
            for c in Currency:
                if c.name in val:
                    val = val.replace(c.name, "")
                    currency = c
            return Money(value=Decimal(val), currency=currency)
        raise ValueError(f"Invalid input to Money type {type(val)} {val}")

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

    # sum starts with 0
    def __radd__(self, other) -> "Money":
        if other == 0:
            return self
        else:
            return self.__add__(other)

    def __add__(self, other) -> "Money":
        return Money(value=self.value + self._cmp_helper(other), currency=self.currency)

    def __sub__(self, other) -> "Money":
        return Money(value=self.value - self._cmp_helper(other), currency=self.currency)

    def __mul__(self, other) -> "Money":
        return Money(value=self.value * self._cmp_helper(other), currency=self.currency)

    def __truediv__(self, other):
        return Money(value=self.value / self._cmp_helper(other), currency=self.currency)

    def __float__(self):
        return float(self.value)

    def __int__(self):
        return int(self.value)

    def __abs__(self):
        return Money(value=abs(self.value), currency=self.currency)

    def __round__(self, n=None):
        return Money(value=Decimal(round(self.value, n)), currency=self.currency)


class IdealPortfolioElement(BaseModel):
    ticker: str
    weight: Decimal


class IdealPortfolio(BaseModel):
    holdings: List[IdealPortfolioElement]
    source_date: Optional[date] = Field(default_factory=date.today)

    def _reweight_portfolio(self):
        weights: Decimal = sum([item.weight for item in self.holdings])

        scaling_factor = Decimal(1) / weights

        for item in self.holdings:
            item.weight = item.weight * scaling_factor

    def exclude(self, exclusion_list: List[str]):
        reweighted = []
        excluded = Decimal(0.0)
        for ticker in exclusion_list:
            reweighted.append(ticker)
            for item in self.holdings:
                if item.ticker == ticker:
                    excluded += item.weight
                    item.weight = Decimal(0.0)

        self.holdings = [
            item for item in self.holdings if item.ticker not in [reweighted]
        ]
        self._reweight_portfolio()
        Logger.info(
            f"Set the following stocks to weight 0 {reweighted}. Total value excluded {excluded}."
        )
        return self

    def reweight(
        self,
        ticker_list: List[str],
        weight: Union[Decimal, float],
        min_weight: Union[Decimal, float] = Decimal(0.005),
    ):
        cweight = Decimal(weight)
        cmin_weight = Decimal(min_weight)
        reweighted = []
        total_value = Decimal(0)
        for ticker in ticker_list:
            found = False
            for item in self.holdings:
                if item.ticker == ticker:
                    total_value += item.weight * cweight
                    item.weight = item.weight * cweight
                    reweighted.append(ticker)
                    found = True
            if not found:
                reweighted.append(ticker)
                total_value += cmin_weight
                self.holdings.append(
                    IdealPortfolioElement(ticker=ticker, weight=cmin_weight)
                )
        self._reweight_portfolio()
        Logger.info(
            f"modified the following by weight {cweight} {reweighted}. Total value modified {total_value}."
        )
        return self

    def reweight_to_present(self, provider: "BaseProvider"):
        imaginary_base = Decimal(1_000_000)
        values = {}
        for item in self.holdings:
            try:
                source_price = provider.get_instrument_price(
                    item.ticker, self.source_date
                )
            except PriceFetchError:
                source_price = provider.get_instrument_price(item.ticker)
            today_price = provider.get_instrument_price(item.ticker)
            if not source_price or not today_price:
                # if we couldn't get a historical price
                # keep the value the same
                values[item.ticker] = imaginary_base * item.weight
                continue
            source_shares = imaginary_base * item.weight / source_price

            today_value = today_price * source_shares
            values[item.ticker] = today_value
        today_value = Decimal(sum(values.values()))

        for item in self.holdings:
            new_weight = values[item.ticker] / today_value
            item.weight = new_weight
        self._reweight_portfolio()

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
    units: Decimal
    value: Money
    weight: Decimal = Decimal(0.0)

    @validator("value", pre=True)
    def value_coercion(cls, v) -> Money:
        return Money.parse(v)


class RealPortfolio(IdealPortfolio):
    holdings: List[RealPortfolioElement]  # type: ignore

    @property
    def _index(self):
        return {val.ticker: val for val in self.holdings}

    def get_holding(self, ticker: str):
        return self._index.get(ticker)

    @property
    def value(self) -> Money:
        values: List[Money] = [item.value for item in self.holdings]
        return Money(value=sum(values))

    def _reweight_portfolio(self):
        value = self.value
        for item in self.holdings:
            item.weight = Decimal(item.value / value.value)

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
