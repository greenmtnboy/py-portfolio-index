from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    CompositePortfolio,
    Money,
)


def test_portfolio():
    assert 1 == 1

    base = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1, value=Money(value=1)),
            RealPortfolioElement(ticker="MSFT", units=1, value=Money(value=1)),
        ]
    )

    assert base.value.value == 2


def test_composite():
    base1 = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1, value=Money(value=1)),
            RealPortfolioElement(ticker="MSFT", units=1, value=Money(value=1)),
        ],
        cash=Money(value=1),
    )

    base2 = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1, value=Money(value=1)),
            RealPortfolioElement(ticker="MSFT", units=1, value=Money(value=1)),
        ],
        cash=Money(value=1),
    )

    composite = CompositePortfolio([base1, base2])

    assert composite.cash == Money(value=2)
