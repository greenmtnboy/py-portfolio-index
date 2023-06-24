from dataclasses import dataclass
from typing import Optional, Dict, Union
from decimal import Decimal
from math import floor, ceil

from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy, RoundingStrategy
from py_portfolio_index.models import Money, OrderElement, OrderPlan, OrderType
from .models import IdealPortfolio, RealPortfolio

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
    real: RealPortfolio,
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


def generate_order_plan(
    real: RealPortfolio,
    ideal: IdealPortfolio,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    # rounding_strategy=RoundingStrategy.CLOSEST,
    target_size: Optional[Money | float | int] = None,
    purchase_power: Optional[Money | float | int] = None,
    min_order_value: Money = MIN_ORDER_MONEY
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
            sell_target = max(sell_target, MIN_ORDER_MONEY )
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
            buy_target = max(buy_target, min_order_value )
            to_purchase.append(
                OrderElement(
                    ticker=key, value=buy_target, qty=None, order_type=OrderType.BUY
                )
            )
            purchase_power = purchase_power - buy_target

        Logger.info(
            f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}"
        )

    return OrderPlan(to_buy=to_purchase, to_sell=to_sell)
