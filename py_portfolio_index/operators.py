from dataclasses import dataclass
from typing import Optional, Dict, Union, Mapping, List, Callable
from decimal import Decimal
from math import floor, ceil
from collections import defaultdict
from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy, RoundingStrategy
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.models import (
    Money,
    ProviderType,
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


def round_int_with_strategy(
    to_buy_currency, rounding_strategy: RoundingStrategy
) -> int:
    if rounding_strategy == RoundingStrategy.CLOSEST:
        to_buy_units = int(round(to_buy_currency, 0))
    elif rounding_strategy == RoundingStrategy.FLOOR:
        to_buy_units = int(floor(to_buy_currency))
    elif rounding_strategy == RoundingStrategy.CEILING:
        to_buy_units = int(ceil(to_buy_currency))
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


def generate_sell_order(
    key: str,
    prices: Dict[str, Decimal | None],
    target_value: Money,
    diffvalue: ComparisonResult,
    provider: ProviderType | None = None,
) -> OrderElement | None:
    if round(diffvalue.diff, 4) == 0.0000:
        return None
    elif diffvalue.diff < 0:
        sell_target: Money = (
            target_value * diffvalue.comparison - target_value * diffvalue.model
        )
        price = prices.get(key)
        if not price:
            return None
        qty = round_int_with_strategy(sell_target / price, RoundingStrategy.FLOOR)
        sell_target = max(sell_target, MIN_ORDER_MONEY)
        return OrderElement(
            ticker=key,
            value=sell_target,
            order_type=OrderType.SELL,
            qty=qty,
            provider=provider,
        )
    return None


def generate_buy_order(
    min_order_value: Money,
    scaling_factor: Money,
    purchase_power: Money,
    buy_order: PurchaseStrategy,
    key: str,
    prices: Dict[str, Decimal | None],
    target_value: Money,
    diffvalue: ComparisonResult,
    provider: ProviderType | None = None,
    fractional_shares: bool = True,
) -> OrderElement | None:
    if purchase_power <= 0:
        Logger.debug("No more money to spend")
        return None
    if round(diffvalue.diff, 4) == 0.0000:
        return None
    elif not diffvalue.diff > 0:
        return None
    diff_text = "Underweight"
    initial_buy_target: Money = Money(
        value=min(
            target_value * diffvalue.model - target_value * diffvalue.comparison,
            purchase_power,
        )
    )
    if buy_order == PurchaseStrategy.PEANUT_BUTTER:
        if initial_buy_target > 0.0:
            max_value: Decimal = max(
                Decimal(float(initial_buy_target.value)) * scaling_factor.decimal,
                Decimal(1.0),
            )
            initial_buy_target = Money(value=max_value)
    initial_buy_target = max(initial_buy_target, min_order_value)
    _price = prices[key]
    if not _price:
        return None
    price = Money(value=_price)

    if not fractional_shares:
        qty = round_int_with_strategy(
            initial_buy_target / price, RoundingStrategy.FLOOR
        )
        if qty == 0:
            return None
        buy_target = None
    else:
        # if we can use fractional, go with the target price only
        qty = None
        buy_target = initial_buy_target

    Logger.debug(
        f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}"
    )
    return OrderElement(
        ticker=key,
        value=buy_target,
        qty=qty,
        price=price,
        order_type=OrderType.BUY,
        provider=provider,
    )


def gen_diff_and_scaling(
    buy_order: PurchaseStrategy,
    output: Dict[str, ComparisonResult],
    purchase_power: Money,
    target_value: Money,
    currently_held: Money,
) -> tuple[Money, Dict[str, ComparisonResult]]:
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
    return scaling_factor, diff_output


def generate_order_plan(
    real: PortfolioProtocol,
    ideal: IdealPortfolio,
    price_fetcher: Callable,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size: Optional[Money | float | int] = None,
    purchase_power: Optional[Money | float | int] = None,
    min_order_value: Money = MIN_ORDER_MONEY,
    skip_tickers: Optional[set[str]] = None,
    fractional_shares: bool = True,
    provider: ProviderType | None = None,
    existing_orders: List[OrderElement] | None = None,
    skip_invalid: bool = True,
    include_sell_orders: bool = False,
) -> OrderPlan:
    diff = Decimal(0.0)
    selling = Decimal(0.0)
    buying = Decimal(0.0)
    target_value: Money = Money(value=target_size) if target_size else real.value
    output: Dict[str, ComparisonResult] = {}
    safe_purchase_power: Money = Money(value=purchase_power or target_value)
    currently_held = Money(value=0)
    current_orders = existing_orders or []
    current_order_val_map: dict[str, Money] = defaultdict(lambda: Money(value=0))
    for current_order in current_orders:
        current_order_val_map[current_order.ticker] += current_order.inferred_value
    for value in ideal.holdings:
        if skip_tickers and value.ticker in skip_tickers:
            continue
        comparison = real.get_holding(value.ticker)
        if not comparison:
            actual_value = Money.parse("0.0")
        else:
            actual_value = comparison.value

        if value.ticker in current_order_val_map:
            actual_value += current_order_val_map[value.ticker]

        if actual_value.is_zero:
            percentage = Decimal(0.0)
        else:
            percentage = Decimal((actual_value / target_value).value)
            actual_value = actual_value

        # track how much we currently have
        currently_held += actual_value
        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = Decimal(value.weight) - percentage
        diff += round(abs(_diff), 4)
        if _diff == 0:
            continue
        elif _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(
        f"Total portfolio % delta {print_per(diff)}. Overweight {print_per(selling)}, underweight {print_per(buying)}, have {purchase_power}"
    )

    scaling_factor, diff_output = gen_diff_and_scaling(
        buy_order, output, safe_purchase_power, target_value, currently_held
    )
    to_purchase: list[OrderElement] = []
    to_sell: list[OrderElement] = []
    price_missing: set[str] = set()
    try:
        prices = price_fetcher([*diff_output.keys()])
    except PriceFetchError as e:
        for x in e.tickers:
            price_missing.add(x)
        Logger.info(
            f"Was unable to fetch prices for {price_missing} tickers, adding to skipped."
        )
        if not skip_invalid:
            raise e
        return generate_order_plan(
            real=real,
            ideal=ideal,
            price_fetcher=price_fetcher,
            buy_order=buy_order,
            target_size=target_size,
            purchase_power=purchase_power,
            min_order_value=min_order_value,
            skip_tickers=(
                skip_tickers.union(price_missing) if skip_tickers else price_missing
            ),
            fractional_shares=fractional_shares,
            provider=provider,
            existing_orders=current_orders,
        )

    for key, diffvalue in diff_output.items():
        sell_order = generate_sell_order(
            key=key,
            prices=prices,
            target_value=target_value,
            diffvalue=diffvalue,
            provider=provider,
        )
        if sell_order and include_sell_orders:
            to_sell.append(sell_order)

    for key, diffvalue in diff_output.items():
        buy_order = generate_buy_order(
            min_order_value=min_order_value,
            scaling_factor=scaling_factor,
            purchase_power=safe_purchase_power,
            buy_order=buy_order,
            key=key,
            prices=prices,
            target_value=target_value,
            diffvalue=diffvalue,
            provider=provider,
            fractional_shares=fractional_shares,
        )
        if buy_order:
            if buy_order.value:
                safe_purchase_power = safe_purchase_power - buy_order.value
            elif buy_order.qty:
                safe_purchase_power = safe_purchase_power - (
                    prices[key] * buy_order.qty
                )
            Logger.debug(f"{safe_purchase_power} left - order is {buy_order}")
            to_purchase.append(buy_order)
        if safe_purchase_power <= 0:
            break

    return OrderPlan(to_buy=to_purchase, to_sell=to_sell)


def generate_composite_order_plan(
    composite: CompositePortfolio,
    ideal: IdealPortfolio,
    purchase_order_maps: Mapping[ProviderType, PurchaseStrategy] | PurchaseStrategy,
    target_size: Optional[Money | float | int],
    min_order_value: Money = MIN_ORDER_MONEY,
    safety_threshold: Decimal = Decimal(0.95),
    target_order_size: Optional[Money] = None,
    include_sell_orders: bool = False,
) -> Mapping[ProviderType, OrderPlan]:
    provider_to_portfolio_map = {
        x.provider: x for x in composite.portfolios if x.provider
    }
    if target_order_size:
        purchase_power_money = {}
        for portfolio in composite.portfolios:
            if portfolio.provider:
                local_power = min(portfolio.cash, target_order_size)
                purchase_power_money[portfolio.provider.PROVIDER] = local_power
                target_order_size -= local_power
    else:
        purchase_power_money = {
            x.provider.PROVIDER: x.cash for x in composite.portfolios if x.provider
        }
    Logger.debug(f"Purchase power money is {purchase_power_money}")
    providers: List[BaseProvider] = list(provider_to_portfolio_map.keys())  # type: ignore

    if isinstance(purchase_order_maps, PurchaseStrategy):
        purchase_order_maps = {x.PROVIDER: purchase_order_maps for x in providers}
    processed = set()
    # check each of our p
    output: defaultdict[ProviderType, OrderPlan] = defaultdict(
        lambda: OrderPlan(to_buy=[], to_sell=[])
    )
    skip_tickers: set[str] = set()
    for provider in providers:
        skip_tickers = skip_tickers.union(provider.get_unsettled_instruments())

    purchase_order = sorted(
        providers, key=lambda x: (x.SUPPORTS_FRACTIONAL_SHARES, x.cash)
    )
    orders: list[OrderElement] = []
    for provider in purchase_order:
        provider_purchase_power: Money = purchase_power_money.get(
            provider.PROVIDER
        ) or Money(value=0)
        processed.add(provider.PROVIDER)
        port = provider_to_portfolio_map[provider]
        # build the plan across the _entire_ composite portfolio

        # if we don't know how much cash we have, skip
        Logger.info(
            f"Doing provider {provider.PROVIDER} with {provider_purchase_power}"
        )
        if not port.cash or port.cash <= Money(value=0):
            Logger.info("No cash left to purchase")
            continue

        local_max_spend = port.cash * safety_threshold
        local_purchase_power = min(provider_purchase_power, local_max_spend)

        purchase_plan: OrderPlan = generate_order_plan(
            ideal=ideal,
            real=composite,
            buy_order=purchase_order_maps[provider.PROVIDER],
            # rounding_strategy=RoundingStrategy.CLOSEST,
            target_size=target_size,
            purchase_power=local_purchase_power,
            min_order_value=min_order_value,
            skip_tickers=skip_tickers,
            fractional_shares=provider.SUPPORTS_FRACTIONAL_SHARES,
            price_fetcher=provider.get_instrument_prices,
            provider=provider.PROVIDER,
            existing_orders=orders,
            include_sell_orders=include_sell_orders,
        )
        orders += purchase_plan.all_orders
        output[provider.PROVIDER] += purchase_plan
    return output


def purchase_composite_order_plan(
    orders: Mapping[ProviderType, OrderPlan],
    providers: list[BaseProvider],
    include_sell_orders: bool = False,
):
    for provider in providers:
        if provider.PROVIDER in orders:
            provider.purchase_order_plan(
                orders[provider.PROVIDER], include_sell_orders=include_sell_orders
            )
        else:
            Logger.info(f"Provider {provider.PROVIDER} has no orders to execute")
    return True
