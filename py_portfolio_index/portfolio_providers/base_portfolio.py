from __future__ import annotations
from math import floor, ceil
from typing import Dict, Union, Optional, Set, List, Callable, Any
from decimal import Decimal
from datetime import date

from py_portfolio_index.common import (
    print_money,
    print_per,
    round_up_to_place,
    get_basic_stock_info,
)
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import RoundingStrategy, ProviderType, OrderType
from py_portfolio_index.exceptions import OrderError
from py_portfolio_index.models import (
    Money,
    OrderPlan,
    OrderElement,
    StockInfo,
    ProfitModel,
    DividendResult,
)
from py_portfolio_index.models import RealPortfolio
from dataclasses import dataclass, field
from datetime import datetime
from py_portfolio_index.portfolio_providers.common import PriceCache
from py_portfolio_index.enums import ObjectKey


@dataclass
class CachedValue:
    value: Any
    fetcher: Callable
    set: datetime = field(default_factory=datetime.now)


class BaseProvider(object):
    PROVIDER: ProviderType = ProviderType.DUMMY
    MIN_ORDER_VALUE = Money(value=1)
    MAX_ORDER_DECIMALS = 2
    SUPPORTS_BATCH_HISTORY = 0
    SUPPORTS_FRACTIONAL_SHARES = True

    def __init__(self, quote_provider: BaseProvider | None = None) -> None:
        self.stock_info_cache: Dict[str, StockInfo] = {}
        self._price_cache: PriceCache = PriceCache(
            fetcher=self._get_instrument_prices,
            single_fetcher=self._get_instrument_price,
        )
        self.CACHE: dict[str, CachedValue] = {}
        self._quote_provider = quote_provider

    def clear_cache(self, skip_clearing: List[str]):
        for value in self.CACHE.values():
            if value in skip_clearing:
                continue
            value.value = None
        if self._quote_provider:
            self._quote_provider.clear_cache(skip_clearing)

    def _get_cached_value(
        self,
        key: ObjectKey,
        value: Optional[Any] = None,
        max_age_seconds: int = 60 * 60,
        callable: Optional[Callable] = None,
    ) -> Any:
        if value:
            skey = f"{key}_{value}"
        else:
            skey = f"{key}"
        if skey in self.CACHE:
            cached = self.CACHE[skey]
        elif callable:
            cached = CachedValue(value=None, fetcher=callable)
            self.CACHE[skey] = cached
        if cached.value:
            age = datetime.now() - cached.set
            if age.seconds < max_age_seconds:
                return cached.value
        cached.value = cached.fetcher()

        return cached.value

    @property
    def valid_assets(self) -> Set[str]:
        return set()

    @property
    def cash(self) -> Money:
        return self.get_holdings().cash or Money(value=0)

    def _get_instrument_price(self, ticker: str, at_day: Optional[date] = None):
        raise NotImplementedError

    def _get_instrument_prices(self, ticker: List[str], at_day: Optional[date] = None):
        raise NotImplementedError

    def get_holdings(self) -> RealPortfolio:
        raise NotImplementedError

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
        raise NotImplementedError

    def get_profit_or_loss(self) -> ProfitModel:
        raw = self.get_per_ticker_profit_or_loss().values()
        appreciation = sum([x.appreciation for x in raw], Money(value=0.0))
        dividends = sum([x.dividends for x in raw], Money(value=0.0))
        return ProfitModel(appreciation=appreciation, dividends=dividends)

    def get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        if self._quote_provider:
            return self._quote_provider.get_instrument_prices(tickers, at_day)
        return self._price_cache.get_prices(tickers=tickers, date=at_day)

    def get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        if self._quote_provider:
            return self._quote_provider.get_instrument_price(ticker, at_day)
        return self._price_cache.get_price(ticker=ticker, date=at_day)

    def buy_instrument(
        self, ticker: str, qty: Decimal, value: Optional[Money] = None
    ) -> bool:
        raise NotImplementedError

    def sell_instrument(
        self, ticker: str, qty: Decimal, value: Optional[Money] = None
    ) -> bool:
        raise NotImplementedError

    def get_unsettled_instruments(self) -> Set[str]:
        raise NotImplementedError

    def purchase_ticker_value_dict(
        self,
        to_buy: Dict[str, Money],
        purchasing_power: Union[Money, Decimal, int, float],
        plan_only: bool = False,
        fractional_shares: bool = True,
        skip_errored_stocks=False,
        rounding_strategy=RoundingStrategy.CLOSEST,
        ignore_unsettled: bool = True,
    ):
        purchased = Money(value=0)
        purchasing_power_resolved = Money(value=purchasing_power)
        target_value: Money = Money(value=sum([v for k, v in to_buy.items()]))
        diff = Money(value=0)
        if ignore_unsettled:
            unsettled = self.get_unsettled_instruments()
        else:
            unsettled = set()
        break_flag = False
        for key, value in to_buy.items():
            if key in unsettled:
                Logger.info(f"Skipping {key} with unsettled orders.")
                continue
            try:
                raw_price = self.get_instrument_price(key)
                if not raw_price:
                    raise ValueError(f"No price found for this instrument: {key}")
                price: Money = Money(value=raw_price)
                Logger.info(f"got price of {price} for {key}")
            except Exception as e:
                if not skip_errored_stocks:
                    raise e
                else:
                    continue
            if price == Money(value=0):
                to_buy_currency = Money(value=0)
            else:
                to_buy_currency = value / price

            if fractional_shares:
                to_buy_units = round(to_buy_currency, 4)
            else:
                if rounding_strategy == RoundingStrategy.CLOSEST:
                    to_buy_units = Money(value=int(round(to_buy_currency, 0)))
                elif rounding_strategy == RoundingStrategy.FLOOR:
                    to_buy_units = Money(value=floor(to_buy_currency))
                elif rounding_strategy == RoundingStrategy.CEILING:
                    to_buy_units = Money(value=ceil(to_buy_currency))
                else:
                    raise ValueError(
                        "Invalid rounding strategy provided with non-fractional shares."
                    )
            if not to_buy_units:
                Logger.info(f"skipping {key} because no units to buy")
                continue
            purchasing = to_buy_units * price

            Logger.info(f"Need to buy {to_buy_units} units of {key}.")
            if (purchasing_power_resolved - purchasing) < Money(value=0):
                Logger.info("Out of money, buying what is possible and exiting")
                break_flag = True
                purchasing = purchasing_power_resolved
                to_buy_units = Decimal(round(purchasing / price, 4).value)
            if to_buy_units > Decimal(0):
                Logger.info(f"going to buy {to_buy_units} of {key}")
                try:
                    if not plan_only:
                        successfully_purchased = self.buy_instrument(key, to_buy_units)
                    if successfully_purchased:
                        purchasing_power_resolved = (
                            purchasing_power_resolved - purchasing
                        )
                        purchased += purchasing
                        diff += abs(value - purchasing)
                        Logger.info(
                            f"bought {to_buy_units} of {key}, {purchasing_power_resolved} left"
                        )
                except Exception as e:
                    print(e)
                    if not skip_errored_stocks:
                        raise e
            if break_flag:
                Logger.info(
                    f"No purchasing power left, purchased {print_money(purchased)} of {print_money(target_value)}."
                )
                break
        Logger.info(
            f"$ diff from ideal for purchased stocks was {print_money(diff)}. {print_per(diff / target_value)} of total purchase goal."
        )

    def handle_order_element(self, element: OrderElement, dry_run: bool = False):
        raw_price = self.get_instrument_price(element.ticker)

        if not raw_price:
            raise OrderError(f"No price found for this instrument: {element.ticker}")
        price: Money = Money(value=raw_price)
        if element.qty:
            units = Decimal(element.qty)
            value = Money(value=units * price.decimal)

        elif element.value:
            raw_price = self.get_instrument_price(element.ticker)
            Logger.info(f"got price of {price} for {element.ticker}")
            units = round_up_to_place(
                (element.value / price).decimal, self.MAX_ORDER_DECIMALS
            )
            value = element.value
        else:
            raise OrderError("Order element must have qty or value")
        if not dry_run:
            if element.order_type == OrderType.BUY:
                self.buy_instrument(element.ticker, units, value)
                Logger.info(f"Bought {units} of {element.ticker}")
            elif element.order_type == OrderType.SELL:
                self.sell_instrument(element.ticker, units, value)
                Logger.info(f"Sold {units} of {element.ticker}")
            else:
                raise OrderError("Invalid order type")

        else:
            Logger.info(f"Would have bought {units} of {element.ticker}")

    def _get_stock_info(self, ticker: str) -> dict:
        raise NotImplementedError

    def get_stock_info(self, ticker: str) -> StockInfo:
        cached = self.stock_info_cache.get(ticker, None)
        if not cached:
            dynamic = self._get_stock_info(ticker)
            basic = get_basic_stock_info(ticker, fail_on_missing=False)
            if basic:
                raw_data = basic.dict()
            else:
                raw_data = {}
            final = StockInfo(**{**raw_data, **dynamic})
            self.stock_info_cache[ticker] = final
            return final
        return cached

    def purchase_order_plan(
        self,
        plan: OrderPlan,
        skip_errored_stocks: bool = False,
        ignore_unsettled: bool = True,
        plan_only: bool = False,
        include_sell_orders: bool = False,
    ):
        if ignore_unsettled:
            unsettled = self.get_unsettled_instruments()
        else:
            unsettled = set()
        for item in plan.to_buy:
            if item.ticker in unsettled:
                Logger.info(f"Skipping {item.ticker} with unsettled orders.")
                continue
            try:
                if item.order_type == OrderType.SELL and not include_sell_orders:
                    continue
                self.handle_order_element(item, dry_run=plan_only)
            except Exception as e:
                Logger.error(f"Failed to purchase {item.ticker}:{str(e)}.")
                if not skip_errored_stocks:
                    raise e

    def refresh(self):
        pass

    def _get_dividends(self):
        raise NotImplementedError

    def get_dividend_details(
        self, start: datetime | None = None
    ) -> list[DividendResult]:
        raise NotImplementedError

    def get_dividend_history(self) -> Dict[str, Money]:
        return self._get_cached_value(ObjectKey.DIVIDENDS, callable=self._get_dividends)

    def _shutdown(self):
        pass

    def shutdown(self):
        return self._shutdown()
