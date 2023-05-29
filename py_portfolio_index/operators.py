from dataclasses import dataclass
from typing import Optional, Dict, Union
from decimal import Decimal

from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.models import Money
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
