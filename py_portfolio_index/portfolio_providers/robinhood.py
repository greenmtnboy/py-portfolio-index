import pandas as pd
import re
from time import sleep
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
        output = self._provider.order_buy_fractional_by_quantity(ticker, qty)
        msg = output.get('detail')
        print(output)

        if msg and 'throttled' in msg:
            print(msg)
            m = re.search('available in ([0-9]+) seconds', msg)
            if m:
                found = m.group(1)
                t = int(found)
            else:
                t = 30

            print(f'was throttled! Sleeping {t}')
            sleep(t)
            self.buy_instrument(ticker=ticker, qty=qty)
        elif msg and 'Too many requests for fractional orders' in msg:
            print(f'was throttled! Sleeping 60')
            sleep(60)
            self.buy_instrument(ticker=ticker, qty=qty)
        if not msg.get('id'):
            print('error')
            print(msg)
            raise ValueError(msg)
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
