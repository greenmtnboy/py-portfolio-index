from py_portfolio_index.models import RealPortfolio, RealPortfolioElement, Money


def test_serialization():
    test_port = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100))
        ]
    )

    x = test_port.json()

    print(x)
