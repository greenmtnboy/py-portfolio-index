from py_portfolio_index.models import RealPortfolio, RealPortfolioElement
from .base_portfolio import BaseProvider
from decimal import Decimal
from typing import Optional
from datetime import date, datetime, timezone, timedelta
from functools import lru_cache
from py_portfolio_index.models import Money
from os import environ


class AlpacaProviderLegacy(BaseProvider):
    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        paper: bool = False,
    ):
        import alpaca_trade_api as tradeapi
        from alpaca_trade_api.common import URL

        if not key_id:
            key_id = environ.get("ALPACA_API_KEY", None)
        if not secret_key:
            secret_key = environ.get("ALPACA_API_SECRET", None)
        if not (key_id and secret_key):
            raise ValueError(
                "Must provide key_id and secret_key or set environment variables ALPACA_API_KEY and ALPACA_API_SECRET "
            )
        TARGET_URL = (
            "https://paper-api.alpaca.markets"
            if paper
            else "https://api.alpaca.markets"
        )
        self.api = tradeapi.REST(
            key_id=key_id, secret_key=secret_key, base_url=URL(TARGET_URL)
        )
        BaseProvider.__init__(self)

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

        if at_day:
            start = datetime(at_day.year, at_day.month, at_day.day, tzinfo=timezone.utc)
            end = start + timedelta(days=7)
            # end = datetime(at_day.year, at_day.month, at_day.day+7, hour=23, tzinfo=timezone.utc)
            raw = self.api.get_bars(
                [ticker],
                timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                start=start.isoformat(),
                end=end.isoformat(),
            )
            # take the first day after target day
            return Decimal(raw[0].h)
        else:
            raw = self.api.get_latest_quote(ticker)
            # if we don't have a value for the current day
            # expand out
            # this is requuired on weekends
            if not raw.ap:
                default = datetime.now(tz=timezone.utc) - timedelta(hours=1)
                start = default - timedelta(days=7)
                end = default
                raw = self.api.get_bars(
                    [ticker],
                    timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
                # take the first day after target day
                return Decimal(raw[0].h)
            return Decimal(raw.ap)

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        qty_float = float(qty)
        self.api.submit_order(
            symbol=ticker,
            qty=qty_float,
            side="buy",
            type="market",
            time_in_force="day",
        )
        return True

    def get_unsettled_instruments(self):
        open_orders = self.api.list_orders(
            status="open", limit=100, nested=True  # show nested multi-leg orders
        )
        return set([o.symbol for o in open_orders])

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
                units=row.qty,
                value=Decimal(row.market_value),
                weight=Decimal(row.market_value) / total_value,
            )
            for row in my_stocks
        ]
        return RealPortfolio(holdings=out)
