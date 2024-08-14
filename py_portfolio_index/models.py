from datetime import date
from typing import (
    List,
    Optional,
    Set,
    Union,
    TYPE_CHECKING,
    Collection,
    runtime_checkable,
)
from pydantic import BaseModel, Field, validator
from py_portfolio_index.enums import Currency, Provider
from py_portfolio_index.constants import Logger
from py_portfolio_index.exceptions import PriceFetchError
from decimal import Decimal
from enum import Enum
from typing import Protocol
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider


@runtime_checkable
class ProviderProtocol(Protocol):
    PROVIDER: Provider = Provider.DUMMY

    def handle_order_element(self, element: "OrderElement") -> bool:
        pass

    def get_unsettled_instruments(self) -> Set[str]:
        pass


class PortfolioProtocol(Protocol):
    @property
    def holdings(self) -> Collection["IdealPortfolioElement"]:
        pass

    @property
    def value(self) -> "Money":
        pass

    def get_holding(self, ticker: str) -> Optional["RealPortfolioElement"]:
        pass


class Money(BaseModel):
    value: Union[Decimal, int, float, "Money"]
    currency: Currency = Currency.USD

    @property
    def decimal(self) -> Decimal:
        return self.value  # type: ignore

    @property
    def is_zero(self):
        return self.value == Decimal(0)

    @validator("value", pre=True)
    def coerce_to_decimal(cls, v) -> Decimal:
        if isinstance(v, (int, float)):
            return Decimal(v)
        elif isinstance(v, Money):
            # TODO convert this
            return v.decimal
        elif isinstance(v, Decimal):
            return v
        return Decimal(v)

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
        elif isinstance(other, int):
            return Decimal(value=other)
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

    def __div__(self, other):
        return Money(value=self.value / self._cmp_helper(other), currency=self.currency)

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


class ProfitModel(BaseModel):
    appreciation: Money
    dividends: Money

    @property
    def total(self):
        return self.appreciation + self.dividends

    def __add__(self, other: "ProfitModel"):
        return ProfitModel(
            appreciation=self.appreciation + other.appreciation,
            dividends=self.dividends + other.dividends,
        )


class IdealPortfolioElement(BaseModel):
    ticker: str
    weight: Decimal


@dataclass
class ReweightResponse:
    original: Decimal
    new: Decimal
    original_price: Decimal | None
    new_price: Decimal | None
    ratio: Decimal


class IdealPortfolio(BaseModel):
    holdings: List[IdealPortfolioElement]
    source_date: Optional[date] = Field(default_factory=date.today)

    def normalize(self):
        """Ensure component weights go to 100"""
        self._reweight_portfolio()

    def add_stock(self, ticker: str, weight: Decimal, rebalance: bool = True):
        new = IdealPortfolioElement(ticker=ticker, weight=weight)
        if any([item.ticker == ticker for item in self.holdings]):
            raise ValueError(f"Stock {ticker} already in portfolio")
        self.holdings.append(new)
        if rebalance:
            self._reweight_portfolio()
        return self

    def contains(self, ticker: str) -> bool:
        return ticker in [item.ticker for item in self.holdings]

    def _reweight_portfolio(self) -> None:
        weights: Decimal = Decimal(sum([item.weight for item in self.holdings]))

        scaling_factor = Decimal(1) / weights
        for item in self.holdings:
            item.weight = item.weight * scaling_factor
        self.holdings = sorted(self.holdings, key=lambda x: x.weight, reverse=True)

    def exclude(self, exclusion_list: List[str]):
        reweighted = []
        excluded = Decimal(0.0)
        for ticker in exclusion_list:
            for item in self.holdings:
                if item.ticker == ticker:
                    reweighted.append(ticker)
                    excluded += item.weight
                    item.weight = Decimal(0.0)

        self.holdings = [
            item for item in self.holdings if item.ticker not in exclusion_list
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

    def reweight_to_present(
        self, provider: "BaseProvider"
    ) -> dict[str, ReweightResponse]:
        if self.source_date == date.today():
            Logger.info("Already reweighted to present")
            return {}
        output = {}
        imaginary_base = Decimal(1_000_000)
        values = {}
        valid_assets = [
            item for item in self.holdings if item.ticker in provider.valid_assets
        ]
        if provider.SUPPORTS_BATCH_HISTORY:
            tickers = [item.ticker for item in valid_assets]
            historic_prices = provider.get_instrument_prices(tickers, self.source_date)
            today_prices = provider.get_instrument_prices(tickers, None)
        else:
            historic_prices = {}
            today_prices = {}
            for item in valid_assets:
                try:
                    historic_prices[item.ticker] = provider.get_instrument_price(
                        item.ticker, self.source_date
                    )
                    today_prices[item.ticker] = provider.get_instrument_price(
                        item.ticker
                    )
                except PriceFetchError:
                    historic_prices[item.ticker] = None
                    today_prices[item.ticker] = None
        for item in self.holdings:
            source_price = historic_prices.get(item.ticker, None)
            today_price = today_prices.get(item.ticker, None)
            if not source_price or not today_price:
                # if we couldn't get a historical price
                # keep the value the same
                values[item.ticker] = imaginary_base * item.weight
                continue
            source_shares = imaginary_base * item.weight / source_price
            stock_value_today = today_price * source_shares
            values[item.ticker] = stock_value_today
        today_value = Decimal(sum(values.values()))

        for item in self.holdings:
            new_weight = values[item.ticker] / today_value
            if item.weight > 0:
                ratio = round(((new_weight - item.weight) / item.weight) * 100, 2)
            else:
                ratio = Decimal(0.0)
            output[item.ticker] = ReweightResponse(
                original=item.weight,
                new=new_weight,
                original_price=historic_prices.get(item.ticker),
                new_price=today_prices.get(item.ticker),
                ratio=ratio,
            )
            item.weight = new_weight
        # change our source date to today
        # so we don't reweight again
        self.source_date = date.today()
        self._reweight_portfolio()
        return output


class RealPortfolioElement(IdealPortfolioElement):
    ticker: str
    units: Decimal
    value: Money
    weight: Decimal = Decimal(0.0)
    unsettled: bool = False
    dividends: Money = Money(value=0, currency=Currency.USD)
    appreciation: Money = Money(value=0, currency=Currency.USD)

    @validator("value", pre=True)
    def value_coercion(cls, v) -> Money:
        return Money.parse(v)

    def __add__(self, other: "RealPortfolioElement"):
        if self.ticker != other.ticker:
            raise ValueError("Cannot add different tickers")
        self.units += other.units
        self.value += other.value
        self.dividends += other.dividends
        self.appreciation += other.appreciation
        return self


class RealPortfolio(BaseModel):
    holdings: List[RealPortfolioElement]
    provider: Optional[ProviderProtocol] = None
    cash: Money = Field(default_factory=lambda: Money(value=0, currency=Currency.USD))
    profit_and_loss: None | ProfitModel = None

    # @property
    # def provider(self) -> Optional["BaseProvider" ]:
    #     return self._provider
    class Config:
        arbitrary_types_allowed = True

    @property
    def _index(self):
        return {val.ticker: val for val in self.holdings}

    def get_holding(self, ticker: str) -> RealPortfolioElement | None:
        return self._index.get(ticker)

    @property
    def value(self) -> Money:
        values: List[Money] = [item.value for item in self.holdings]
        if self.cash:
            values += [self.cash]
        return Money(value=sum(values))

    def _reweight_portfolio(self):
        value = self.value
        if value.decimal.is_zero():
            return
        for item in self.holdings:
            item.weight = Decimal(item.value.value / value.value)

    def add_holding(self, holding: RealPortfolioElement, reweight: bool = True):
        existing = self._index.get(holding.ticker)
        if existing:
            existing = existing + holding
        if not existing:
            self.holdings.append(
                RealPortfolioElement(
                    ticker=holding.ticker,
                    weight=holding.weight,
                    units=holding.units,
                    value=holding.value,
                    unsettled=False,
                    dividends=holding.dividends,
                    appreciation=holding.appreciation,
                )
            )
        if reweight:
            self._reweight_portfolio()

    def __add__(self, other):
        if isinstance(other, RealPortfolioElement):
            self.add_holding(other)
        elif isinstance(other, RealPortfolio):
            for item in other.holdings:
                self.add_holding(item, reweight=False)
            self._reweight_portfolio()
        else:
            raise ValueError(f"{type(other)} cannot be added to portfolio element")
        return self

    def refresh(self):
        if self.provider:
            new = self.provider.get_holdings()
            self.holdings = new.holdings
            self.cash = new.cash
            self._reweight_portfolio()
        else:
            raise ValueError("Cannot refresh real portfolio with no provider")


class CompositePortfolio:
    """Provides a view on children portfolios, to enable planning
    across multiple providers"""

    def __init__(self, portfolios: List[RealPortfolio]):
        self.portfolios: List[RealPortfolio] = portfolios
        self._internal_base = RealPortfolio(holdings=[])
        self.rebuild_cache()

    @property
    def cash(self) -> Money:
        return Money(
            value=sum([item.cash for item in self.portfolios if item.cash is not None])
        )

    def rebuild_cache(self):
        new = RealPortfolio(holdings=[])
        for item in self.portfolios:
            new += item
        self._internal_base = new

    @property
    def internal_base(self):
        return self._internal_base

    @property
    def value(self) -> Money:
        return self.internal_base.value

    @property
    def holdings(self) -> List[RealPortfolioElement]:
        return self.internal_base.holdings

    def get_holding(self, ticker: str) -> RealPortfolioElement | None:
        return self.internal_base.get_holding(ticker)

    def get_provider_portfolio(self, provider: "Provider") -> RealPortfolio:
        for port in self.portfolios:
            if port.provider and port.provider.PROVIDER == provider:
                return port
        raise ValueError(f"Could not find provider {provider}")


class OrderType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderElement(BaseModel):
    ticker: str
    order_type: OrderType
    value: Money | None
    qty: float | int | None
    price: Money | None = None
    provider: Optional[Provider] = None

    @property
    def inferred_value(self) -> Money:
        if self.value:
            return self.value
        if self.qty and self.price:
            return self.price * self.qty
        raise ValueError("Cannot infer value")

    def __add__(self, other):
        if not isinstance(other, OrderElement):
            raise ValueError(f"Cannot add {type(other)} to OrderElement")
        if self.ticker != other.ticker:
            raise ValueError("Cannot add different tickers")
        if self.order_type != other.order_type:
            raise ValueError("Cannot add different order types")
        if self.value and other.value:
            return OrderElement(
                ticker=self.ticker,
                order_type=self.order_type,
                value=self.value + other.value,
            )
        elif self.qty and other.qty:
            return OrderElement(
                ticker=self.ticker,
                order_type=self.order_type,
                qty=self.qty + other.qty,
            )
        else:
            raise ValueError("Cannot add value and qty based orders")


class OrderPlan(BaseModel):
    to_buy: List[OrderElement]
    to_sell: List[OrderElement]

    @property
    def all_orders(self) -> List[OrderElement]:
        return self.to_buy + self.to_sell

    @property
    def tickers(self):
        output = set()
        for x in self.to_buy:
            output.add(x.ticker)
        for y in self.to_sell:
            output.add(y.ticker)
        return output

    def __add__(self, other: "OrderPlan"):
        if other == 0:
            return self
        if not isinstance(other, OrderPlan):
            raise ValueError(f"Cannot add {type(other)} to OrderPlan")
        for x in other.to_buy:
            found = False
            for y in self.to_buy:
                if x.ticker == y.ticker:
                    x = x + y
                    found = True
                    break
            if not found:
                self.to_buy.append(x)

        for sx in other.to_sell:
            found = False
            for sy in self.to_sell:
                if sx.ticker == sy.ticker:
                    sx = sx + sy
                    found = True
                    break
            if not found:
                self.to_sell.append(sx)
        return self


class LoginResponseStatus(Enum):
    MFA_REQUIRED = 1
    CHALLENGE_REQUIRED = 2
    SUCCESS = 3


@dataclass
class LoginResponse:
    status: LoginResponseStatus
    data: dict = field(default_factory=dict)


class StockInfo(BaseModel):
    ticker: str
    name: str | None = None
    country: str | None = None
    currency: str | None = None
    exchange: str | None = None
    industry: str | None = None
    sector: str | None = None
    location: str | None = None
    cusip: str | None = None
    cik: int | None = None
    sic_num: int | None = None
    sic_description: str | None = None
    description: str | None = None
    website: str | None = None
    category: str | None = None
    tradable: bool | None = None
    tags: List[str] = Field(default_factory=list)
    indexes: List[str] = Field(default_factory=list)
