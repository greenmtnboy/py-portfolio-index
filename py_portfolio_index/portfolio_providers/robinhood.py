import re
from decimal import Decimal
from time import sleep
from datetime import date, datetime
import pandas as pd
from typing import Optional

from py_portfolio_index.constants import Logger
from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider
from py_portfolio_index.exceptions import PriceFetchError
from functools import lru_cache
from os import environ

FRACTIONAL_SLEEP = 60


def nearest_value(all_historicals, pivot) -> Optional[dict]:
    filtered = [z for z in all_historicals if z]
    if not filtered:
        return None
    return min(
        filtered,
        key=lambda x: abs(
            datetime.strptime(x["begins_at"], "%Y-%m-%dT%H:%M:%SZ").date() - pivot
        ),
    )


class RobinhoodProvider(BaseProvider):
    """Provider for interacting with Robinhood portfolios.

    Requires username and password.

    """

    def __init__(self, username: str | None = None, password: str | None = None):
        import robin_stocks.robinhood as r

        if not username:
            username = environ.get("ROBINHOOD_USERNAME", None)
        if not password:
            password = environ.get("ROBINHOOD_PASSWORD", None)
        if not (username and password):
            raise ValueError(
                "Must provide username and password arguments or set environment variables ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD "
            )
        self._provider = r
        BaseProvider.__init__(self)
        self._provider.login(username=username, password=password)

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        if at_day:
            historicals = self._provider.get_stock_historicals(
                [ticker], interval="day", span="year", bounds="regular"
            )
            closest = nearest_value(historicals, at_day)
            if closest:
                return Decimal(closest["high_price"])
            raise PriceFetchError(
                f"No historical data found for ticker {ticker} on date {at_day.isoformat()}"
            )
        else:
            quotes = self._provider.get_quotes([ticker])
            if not quotes[0]:
                return None
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
            Logger.info(
                f"RH error: was throttled on fractional orders! Sleeping {FRACTIONAL_SLEEP}"
            )
            sleep(FRACTIONAL_SLEEP)
            output = self.buy_instrument(ticker=ticker, qty=qty)
        if not output.get("id"):
            Logger.error(msg)
            raise ValueError(msg)
        return True

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
