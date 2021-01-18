import pandas as pd

from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider
from py_portfolio_index.exceptions import PriceFetchError


class AlpacaProvider(BaseProvider):
    def __init__(self, key_id: str, secret_key: str, paper: bool = False):
        import alpaca_trade_api as tradeapi
        from alpaca_trade_api.common import URL

        TARGET_URL = (
            "https://paper-api.alpaca.markets"
            if paper
            else "https://api.alpaca.markets"
        )
        self.api = tradeapi.REST(
            key_id=key_id, secret_key=secret_key, base_url=URL(TARGET_URL)
        )
        BaseProvider.__init__(self)

    def _get_instrument_price(self, ticker: str):
        raw = self.api.get_last_quote(ticker)
        return float(raw.askprice)

    def buy_instrument(self, ticker: str, qty: int):
        self.api.submit_order(
            symbol=ticker, qty=qty, side="buy", type="market", time_in_force="day"
        )

    def get_holdings(self):
        my_stocks = self.api.list_positions()
        for val in my_stocks:
            print(val)
        df = pd.DataFrame(my_stocks)
        if df.empty:
            return RealPortfolio(holdings=[])
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
