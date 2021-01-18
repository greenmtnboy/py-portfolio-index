import pandas as pd

from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider


class RobinhoodProvider(BaseProvider):
    def __init__(self, username: str, password: str):
        import robin_stocks as r

        self._provider = r
        BaseProvider.__init__(self)
        self._provider.login(username=username, password=password)

    def get_instrument_price(self, ticker: str):
        quotes = self._provider.get_quotes([ticker])
        if not quotes[0]:
            return False
        return float(quotes[0]["ask_price"])

    def buy_instrument(self, ticker: str, qty: int):
        self._provider.order_buy_fractional_by_quantity(ticker, qty)

    def get_holdings(self):
        my_stocks = self._provider.build_holdings()

        df = pd.DataFrame(my_stocks)
        df = df.T
        df["ticker"] = df.index
        df = df.reset_index(drop=True)
        df.sort_values(by=["percentage"], inplace=True)
        out = [
            RealPortfolioElement(
                ticker=row.ticker,
                units=int(float(row.quantity)),
                value=float(row.equity),
                weight=float(row.percentage) / 100,
            )
            for row in df.itertuples()
        ]
        return RealPortfolio(holdings=out)
