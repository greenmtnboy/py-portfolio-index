from dataclasses import dataclass
from typing import Optional, Dict, Union, Mapping, List
from decimal import Decimal
from math import floor, ceil
from collections import defaultdict

from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy, RoundingStrategy
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.models import (
    Money,
    Provider,
    OrderElement,
    OrderPlan,
    OrderType,
    PortfolioProtocol,
    CompositePortfolio,
)
from .models import IdealPortfolio

MIN_ORDER_SIZE = 2
MIN_ORDER_MONEY = Money(value=MIN_ORDER_SIZE)


@dataclass
class ComparisonResult:
    ticker: str
    model: Decimal
    comparison: Decimal
    actual: Money

    @property
    def diff(self):
        return self.model - Decimal(self.comparison)


def compare_portfolios(
    real: PortfolioProtocol,
    ideal: IdealPortfolio,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size: Optional[Union[Decimal, int]] = None,
):
    output: Dict[str, ComparisonResult] = {}
    diff = Decimal(0.0)
    selling = Decimal(0.0)
    buying = Decimal(0.0)
    target_value: Money = (
        Money(value=Decimal(target_size)) if target_size else real.value
    )
    for value in ideal.holdings:
        comparison = real.get_holding(value.ticker)
        if not comparison:
            percentage = Decimal(0.0)
            actual_value = Money.parse("0.0")
        else:
            percentage = Decimal((comparison.value / target_value).value)
            actual_value = comparison.value
        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = Decimal(value.weight) - percentage
        diff += abs(_diff)
        if _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(
        f"Total portfolio % delta {print_per(diff)}. Overweight {print_per(selling)}, underweight {print_per(buying)}"
    )
    if buy_order == PurchaseStrategy.LARGEST_DIFF_FIRST:
        diff_output: Dict[str, ComparisonResult] = {
            k: v for k, v in sorted(output.items(), key=lambda item: -abs(item[1].diff))
        }
    elif buy_order == PurchaseStrategy.CHEAPEST_FIRST:
        diff_output = {
            k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))
        }
    else:
        raise ValueError("Invalid purchase strategy")
    to_purchase = {}
    to_sell = {}
    for key, diffvalue in diff_output.items():
        if diffvalue.diff == 0:
            continue
        elif diffvalue.diff < 0:
            diff_text = "Overweight"
            to_sell[key] = (
                target_value * diffvalue.comparison - target_value * diffvalue.model
            )
        else:
            diff_text = "Underweight"
            to_purchase[key] = (
                target_value * diffvalue.model - target_value * diffvalue.comparison
            )
            # to_purchase.append(key)
        Logger.info(
            f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}"
        )
    return to_purchase, to_sell


def round_with_strategy(to_buy_currency, rounding_strategy: RoundingStrategy) -> Money:
    if rounding_strategy == RoundingStrategy.CLOSEST:
        to_buy_units = Money(value=int(round(to_buy_currency, 0)))
    elif rounding_strategy == RoundingStrategy.FLOOR:
        to_buy_units = Money(value=floor(to_buy_currency))
    elif rounding_strategy == RoundingStrategy.CEILING:
        to_buy_units = Money(value=ceil(to_buy_currency))
    else:
        raise ValueError("Invalid Rounding Strategy")
    return to_buy_units


def generate_auto_target_size(
    real: CompositePortfolio,
    ideal: IdealPortfolio,
) -> Money:
    cash = Money(value=0)
    for input in real.portfolios:
        cash += input.cash
    in_portfolio_value = Money(value=0)
    for value in ideal.holdings:
        comparison = real.get_holding(value.ticker)
        if not comparison:
            continue
        else:
            in_portfolio_value += comparison.value
    return in_portfolio_value + cash


def generate_composite_order_plan(
    composite: CompositePortfolio,
    ideal: IdealPortfolio,
    purchase_order_maps: Mapping[Provider, PurchaseStrategy] | PurchaseStrategy,
    # rounding_strategy=RoundingStrategy.CLOSEST,
    purchase_power: Optional[Money | float | int] = None,
    target_size: Optional[Money | float | int] = None,
    min_order_value: Money = MIN_ORDER_MONEY,
    safety_threshold: Decimal = Decimal(0.95),
) -> Mapping[Provider, OrderPlan]:
    provider_map = {x.provider: x for x in composite.portfolios if x.provider}
    providers: List[BaseProvider] = list(provider_map.keys())  # type: ignore

    if isinstance(purchase_order_maps, PurchaseStrategy):
        purchase_order_maps = {x.PROVIDER: purchase_order_maps for x in providers}
    processed = set()
    # check each of our p
    output: defaultdict[Provider, OrderPlan] = defaultdict(
        lambda: OrderPlan(to_buy=[], to_sell=[])
    )
    skip_tickers: set[str] = set()
    for provider in providers:
        skip_tickers = skip_tickers.union(provider.get_unsettled_instruments())

    purchase_power_money = Money(value=purchase_power) if purchase_power else None

    while providers:
        provider = providers.pop()
        if purchase_power_money and purchase_power_money < Money(value=0):
            Logger.info("No dollars left to purchase")
            continue
        Logger.info(f"Beginning plan for {provider}")
        processed.add(provider.PROVIDER)
        port = provider_map[provider]
        # build the plan across the _entire_ composite portfolio

        # if we don't know how much cash we have, skip
        print(f"doing provider {provider} with {port.cash}")
        if not port.cash:
            continue

        local_max_spend = port.cash * safety_threshold
        if purchase_power_money:
            local_purchase_power = min(
                purchase_power_money * safety_threshold, local_max_spend
            )
            purchase_power_money = purchase_power_money - local_purchase_power
        else:
            local_purchase_power = port.cash * safety_threshold

        purchase_plan = generate_order_plan(
            ideal=ideal,
            real=composite,
            buy_order=purchase_order_maps[provider.PROVIDER],
            # rounding_strategy=RoundingStrategy.CLOSEST,
            target_size=target_size,
            purchase_power=local_purchase_power,
            min_order_value=min_order_value,
            skip_tickers=skip_tickers,
        )
        for ticker in purchase_plan.tickers:
            skip_tickers.add(ticker)
        output[provider.PROVIDER] += purchase_plan
    return output


def generate_order_plan(
    real: PortfolioProtocol,
    ideal: IdealPortfolio,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    # rounding_strategy=RoundingStrategy.CLOSEST,
    target_size: Optional[Money | float | int] = None,
    purchase_power: Optional[Money | float | int] = None,
    min_order_value: Money = MIN_ORDER_MONEY,
    skip_tickers: Optional[set[str]] = None
    # fractional_shares: bool = True,
) -> OrderPlan:
    diff = Decimal(0.0)
    selling = Decimal(0.0)
    buying = Decimal(0.0)
    target_value: Money = Money(value=target_size) if target_size else real.value
    output: Dict[str, ComparisonResult] = {}
    purchase_power = Money(value=purchase_power or target_value)
    currently_held = Money(value=0)
    for value in ideal.holdings:
        if skip_tickers and value.ticker in skip_tickers:
            continue
        comparison = real.get_holding(value.ticker)
        if not comparison:
            percentage = Decimal(0.0)
            actual_value = Money.parse("0.0")
        else:
            percentage = Decimal((comparison.value / target_value).value)
            actual_value = comparison.value

        # track how much we currently have
        currently_held += actual_value
        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = Decimal(value.weight) - percentage
        diff += abs(_diff)
        if _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(
        f"Total portfolio % delta {print_per(diff)}. Overweight {print_per(selling)}, underweight {print_per(buying)}"
    )

    scaling_factor = Money(value=1.0)

    if buy_order == PurchaseStrategy.LARGEST_DIFF_FIRST:
        diff_output: Dict[str, ComparisonResult] = {
            k: v for k, v in sorted(output.items(), key=lambda item: -abs(item[1].diff))
        }
    elif buy_order == PurchaseStrategy.CHEAPEST_FIRST:
        diff_output = {
            k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))
        }
    elif buy_order == PurchaseStrategy.PEANUT_BUTTER:
        # divide the difference between where we want to be
        # and where we are
        # across all stocks
        scaling_factor = purchase_power / (target_value - currently_held)

        diff_output = {
            k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))
        }
    else:
        raise ValueError("Invalid purchase strategy")
    to_purchase: list[OrderElement] = []
    to_sell: list[OrderElement] = []

    # first sell everything
    for key, diffvalue in diff_output.items():
        if diffvalue.diff == 0:
            continue
        elif diffvalue.diff < 0:
            diff_text = "Overweight"
            sell_target: Money = (
                target_value * diffvalue.comparison - target_value * diffvalue.model
            )
            if buy_order == PurchaseStrategy.PEANUT_BUTTER:
                sell_target = sell_target * scaling_factor
            # if not fractional_shares:
            #     price = real.get_instrument_price(key)
            #     qty = round_with_strategy(target/price, rounding_strategy)
            #     target =
            sell_target = max(sell_target, MIN_ORDER_MONEY)
            to_sell.append(
                OrderElement(
                    ticker=key, value=sell_target, order_type=OrderType.SELL, qty=None
                )
            )
            #     target_value * diffvalue.comparison - target_value * diffvalue.model
            # )

    for key, diffvalue in diff_output.items():
        if purchase_power <= 0:
            break
        elif diffvalue.diff == 0:
            continue
        elif diffvalue.diff > 0:
            diff_text = "Underweight"
            buy_target: Money = Money(
                value=min(
                    target_value * diffvalue.model
                    - target_value * diffvalue.comparison,
                    purchase_power,
                )
            )
            if buy_order == PurchaseStrategy.PEANUT_BUTTER:
                if buy_target > 0.0:
                    max_value: Decimal = max(
                        Decimal(float(buy_target.value)) * scaling_factor.decimal,
                        Decimal(1.0),
                    )
                    buy_target = Money(value=max_value)
            buy_target = max(buy_target, min_order_value)
            to_purchase.append(
                OrderElement(
                    ticker=key, value=buy_target, qty=None, order_type=OrderType.BUY
                )
            )
            purchase_power = purchase_power - buy_target

        Logger.debug(
            f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}"
        )

    return OrderPlan(to_buy=to_purchase, to_sell=to_sell)
