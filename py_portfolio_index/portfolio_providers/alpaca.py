import pandas as pd

from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider


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
        from decimal import Decimal

        my_stocks = self.api.list_positions()
        # df = pd.DataFrame(my_stocks)
        if not my_stocks:
            return RealPortfolio(holdings=[])
        total_value = sum([Decimal(item.market_value) for item in my_stocks])
        out = [
            RealPortfolioElement(
                ticker=row.symbol,
                units=int(row.qty),
                value=Decimal(row.market_value),
                weight=Decimal(row.market_value) / total_value,
            )
            for row in my_stocks
        ]
        return RealPortfolio(holdings=out)
