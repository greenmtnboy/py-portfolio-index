from py_portfolio_index.operators import generate_order_plan
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
    IdealPortfolio,
    IdealPortfolioElement,
)
from py_portfolio_index.enums import PurchaseStrategy


def test_generate_order_plan():
    real_port = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100))
        ]
    )

    ideal_port = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )

    order_plan = generate_order_plan(
        real_port,
        ideal_port,
        target_size=1000,
        buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    )

    expected = {"AAPL": Money(value=400), "MSFT": Money(value=500)}

    for x in order_plan.to_buy:
        assert x.value == expected[x.ticker]
