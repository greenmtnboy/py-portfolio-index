from py_portfolio_index.models import RealPortfolio, RealPortfolioElement, Money
from py_portfolio_index.exceptions import ConfigurationError, OrderError
from .base_portfolio import BaseProvider
from decimal import Decimal
from typing import Optional
from datetime import date, datetime, timezone, timedelta
from functools import lru_cache


from os import environ


class AlpacaProvider(BaseProvider):
    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        paper: bool = False,
    ):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        if not key_id:
            key_id = environ.get("ALPACA_API_KEY", None)
        if not secret_key:
            secret_key = environ.get("ALPACA_API_SECRET", None)
        if not (key_id and secret_key):
            raise ConfigurationError(
                "Must provide key_id and secret_key or set environment variables ALPACA_API_KEY and ALPACA_API_SECRET "
            )
        self.trading_client = TradingClient(
            api_key=key_id, secret_key=secret_key, paper=False
        )
        self.historical_client = StockHistoricalDataClient(
            api_key=key_id, secret_key=secret_key
        )
        # tradeapi.REST(
        #     key_id=key_id, secret_key=secret_key, base_url=URL(TARGET_URL)
        # )
        BaseProvider.__init__(self)

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest

        if at_day:
            start = datetime(at_day.year, at_day.month, at_day.day, tzinfo=timezone.utc)
            end = start + timedelta(days=7)
            # end = datetime(at_day.year, at_day.month, at_day.day+7, hour=23, tzinfo=timezone.utc)
            raw = self.historical_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=ticker,
                    start=start,
                    end=end,
                    timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    limit=100,
                    adjustment=None,
                    feed=None,
                )
                # [ticker],
                # timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                # start=start.isoformat(),
                # end=end.isoformat(),
            )
            # take the first day after target day
            return Decimal(raw[ticker][0].high)
        else:
            raw = self.historical_client.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=ticker, feed=None)
            )
            # if we don't have a value for the current day
            # expand out
            # this is requuired on weekends
            if not raw[ticker].ask_price:
                default = datetime.now(tz=timezone.utc) - timedelta(hours=1)
                start = default - timedelta(days=7)
                end = default
                raw = self.historical_client.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols=ticker,
                        start=start,
                        end=end,
                        timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                        feed=None,
                        adjustment=None,
                        limit=1000,
                    )
                    # [ticker],
                    # timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    # start=start.isoformat(),
                    # end=end.isoformat(),
                )
                # take the first day after target day
                return Decimal(raw[ticker][0].high)
            return Decimal(raw[ticker].ask_price)

    def buy_instrument(self, ticker: str, qty: Decimal):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.common.exceptions import APIError

        market_order_data = MarketOrderRequest(
            symbol=ticker,
            qty=float(qty),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            self.trading_client.submit_order(order_data=market_order_data)
        except APIError as e:
            import json
            error = json.loads(e._error)
            message = error.get('message', 'Unknown Error')
            raise OrderError(message= f"Failed to buy {ticker} {qty} {e}: {message}")
        return True

    def get_unsettled_instruments(self):
        from alpaca.trading.requests import GetOrdersRequest, QueryOrderStatus

        open_orders = self.trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        return set([o.symbol for o in open_orders])

    def get_holdings(self):
        from decimal import Decimal

        my_stocks = self.trading_client.get_all_positions()
        account = self.trading_client.get_account()
        unsettled = self.get_unsettled_instruments()
        unsettled_elements = [
            RealPortfolioElement(
                ticker=ticker,
                units=0,
                value=Money(value=Decimal(0)),
                weight=Decimal(0),
                unsettled=True,
            )
            for ticker in unsettled
        ]
        if not my_stocks:
            return RealPortfolio(
                holdings=unsettled_elements, cash=Money(value=account.cash)
            )
        total_value = sum([Decimal(item.market_value) for item in my_stocks])
        out = [
            RealPortfolioElement(
                ticker=row.symbol,
                units=row.qty,
                value=Money(value=Decimal(row.market_value)),
                weight=Decimal(row.market_value) / total_value,
                unsettled=row.symbol in unsettled,
            )
            for row in my_stocks
        ]

        extra_unsettled = [
            item
            for item in unsettled_elements
            if item.ticker not in [x.ticker for x in out]
        ]
        out.extend(extra_unsettled)
        return RealPortfolio(holdings=out, cash=Money(value=account.cash))
