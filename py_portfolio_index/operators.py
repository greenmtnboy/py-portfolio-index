from dataclasses import dataclass
from typing import Optional

from py_portfolio_index.common import print_float_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import BuyOrder
from py_portfolio_index.models import Money
from .models import IdealPortfolio, RealPortfolio


@dataclass
class ComparisonResult:
    ticker: str
    model: float
    comparison: float
    actual: Money

    @property
    def diff(self):
        return self.model - self.comparison


def compare_portfolios(
    real: RealPortfolio,
    ideal: IdealPortfolio,
    buy_order=BuyOrder.LARGEST_DIFF_FIRST,
    target_size: Optional[float] = None,
):
    output = {}
    diff = 0
    selling = 0
    buying = 0
    target_value = target_size or real.value
    for value in ideal.holdings:
        comparison = real.get_holding(value.ticker)
        if not comparison:
            percentage = 0
            actual_value = 0
        else:
            percentage = comparison.weight
            actual_value = comparison.value
        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = value.weight - percentage
        diff += abs(_diff)
        if _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(
        f"Total portfolio % delta {print_float_per(diff)}. Overweight {print_float_per(selling)}, underweight {print_float_per(buying)}"
    )
    if buy_order == BuyOrder.LARGEST_DIFF_FIRST:
        output = {
            k: v for k, v in sorted(output.items(), key=lambda item: -abs(item[1].diff))
        }
    elif buy_order == BuyOrder.CHEAPEST_FIRST:
        output = {
            k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))
        }
    else:
        raise ValueError("Invalid purchase strategy")
    to_purchase = {}
    to_sell = {}
    for key, value in output.items():
        if value.diff == 0:
            continue
        elif value.diff < 0:
            diff = "Overweight"
            to_sell[key] = target_value * value.comparison - target_value * value.model
        else:
            diff = "Underweight"
            to_purchase[key] = (
                target_value * value.model - target_value * value.comparison
            )
            # to_purchase.append(key)
        Logger.info(
            f"{diff} {key}, {print_float_per(value.model)} target vs {print_float_per(value.comparison)} actual. Should be ${target_value * value.model}, is ${value.actual}"
        )
    return to_purchase, to_sell
