import re
from decimal import Decimal
from time import sleep

import pandas as pd

from py_portfolio_index.constants import Logger
from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider

FRACTIONAL_SLEEP = 60


class RobinhoodProvider(BaseProvider):
    '''Provider for interacting with Robinhood portfolios.

    Requires username and password.

    '''

    def __init__(self, username: str, password: str):
        import robin_stocks as r

        self._provider = r
        BaseProvider.__init__(self)
        self._provider.login(username=username, password=password)

    def get_instrument_price(self, ticker: str):
        quotes = self._provider.get_quotes([ticker])
        if not quotes[0]:
            return False
        return Decimal(quotes[0]["ask_price"])

    def buy_instrument(self, ticker: str, qty: Decimal):
        float_qty = float(qty)
        output = self._provider.order_buy_fractional_by_quantity(ticker, float_qty)
        msg = output.get("detail")
        if msg and "throttled" in msg:
            m = re.search("available in ([0-9]+) seconds", msg)
            if m:
                found = m.group(1)
                t = int(found)
            else:
                t = 30

            Logger.info(f"RH error: was throttled! Sleeping {t}")
            sleep(t)
            output = self.buy_instrument(ticker=ticker, qty=qty)
        elif msg and "Too many requests for fractional orders" in msg:
            Logger.info(f"RH error: was throttled on fractional orders! Sleeping {FRACTIONAL_SLEEP}")
            sleep(FRACTIONAL_SLEEP)
            output = self.buy_instrument(ticker=ticker, qty=qty)
        if not output.get("id"):
            Logger.error(msg)
            raise ValueError(msg)
        return output

    def get_unsettled_instruments(self):
        orders = self._provider.get_all_open_stock_orders()
        for item in orders:
            item["symbol"] = self._provider.get_symbol_by_url(item["instrument"])
        return set(item["symbol"] for item in orders)

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
                units=Decimal(row.quantity),
                value=Decimal(row.equity),
                weight=Decimal(row.percentage) / 100,
            )
            for row in df.itertuples()
        ]
        return RealPortfolio(holdings=out)
