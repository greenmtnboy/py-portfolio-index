from py_portfolio_index.models import RealPortfolioElement, Money
from decimal import Decimal

try:
    elem = RealPortfolioElement(ticker="AAPL", units=Decimal("10"), value=Money(value=1500))
    print("Success")
except Exception as e:
    print(f"Failed: {e}")
