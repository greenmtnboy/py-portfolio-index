from dataclasses import dataclass
from typing import Optional, Dict, Union
from decimal import Decimal

from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.models import Money, OrderElement, OrderPlan, OrderType
from .models import IdealPortfolio, RealPortfolio


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
    target_value: Money = Money(value=Decimal(target_size)) if target_size else real.value
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


def generate_order_plan(
    real: RealPortfolio,
    ideal: IdealPortfolio,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size: Optional[Money | float | int] = None,
    purchase_power: Optional[Money | float | int] = None,
    fractional_shares: bool = True,
)->OrderPlan:

    if not fractional_shares:
        raise ValueError('Order plans are only supported for fractional shares currently!')
    diff = Decimal(0.0)
    selling = Decimal(0.0)
    buying = Decimal(0.0)
    target_value: Money = Money(value=target_size) if target_size else real.value
    output: Dict[str, ComparisonResult] = {}
    purchase_power = Money(value = purchase_power or target_value)
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
    to_purchase:list[OrderElement] = []
    to_sell:list[OrderElement] = []

    # first sell everything
    for key, diffvalue in diff_output.items():
        if diffvalue.diff == 0:
            continue
        elif diffvalue.diff < 0:
            diff_text = "Overweight"
            to_sell.append(OrderElement(ticker=key, 
                                        value=target_value * diffvalue.comparison - target_value * diffvalue.model, 
                                        order_type=OrderType.SELL))
            # to_sell[key] = (
            #     target_value * diffvalue.comparison - target_value * diffvalue.model
            # )

    for key, diffvalue in diff_output.items():
        if purchase_power<=0:
            break
        elif diffvalue.diff == 0:
            continue
        elif diffvalue.diff > 0:
            diff_text = "Underweight"
            # to_purchase[key] = (
            #     target_value * diffvalue.model - target_value * diffvalue.comparison
            # )   
            target = min(target_value * diffvalue.model - target_value * diffvalue.comparison, purchase_power)
            to_purchase.append(OrderElement(ticker=key,
                                            value=target,
                                            order_type=OrderType.BUY))
            purchase_power = purchase_power-target
            

        Logger.info(
            f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}"
        )

    return OrderPlan(to_buy=to_purchase, to_sell=to_sell)