from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
)
from py_portfolio_index.exceptions import ConfigurationError, OrderError
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from decimal import Decimal
from typing import Optional, Dict, List, Set
from datetime import date, datetime, timezone, timedelta
from functools import lru_cache
from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.enums import Provider
from os import environ

MAX_OPEN_ORDER_SIZE = 500


def filter_prices_response(
    ticker: str, response, earliest: bool = True
) -> Decimal | None:
    try:
        ticker_vals = response[ticker]
    except KeyError:
        ticker_vals = []
    if not earliest:
        ticker_vals = reversed(ticker_vals)
    for x in ticker_vals:
        if x.high:
            return Decimal(x.high)
    return None


class AlpacaProvider(BaseProvider):
    SUPPORTS_BATCH_HISTORY = 50
    PROVIDER = Provider.ALPACA

    API_KEY_VARIABLE = "ALPACA_API_KEY"
    API_SECRET_VARIABLE = "ALPACA_API_SECRET"

    LEGACY_BASE = "https://api.alpaca.markets"

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        paper: bool = False,
    ):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        if not key_id:
            key_id = environ.get(self.API_KEY_VARIABLE, None)
        if not secret_key:
            secret_key = environ.get(self.API_SECRET_VARIABLE, None)
        if not (key_id and secret_key):
            raise ConfigurationError(
                f"Must provide key_id and secret_key or set environment variables {self.API_KEY_VARIABLE} and {self.API_SECRET_VARIABLE}"
            )
        self.trading_client: TradingClient = TradingClient(
            api_key=key_id, secret_key=secret_key, paper=paper
        )
        self.historical_client = StockHistoricalDataClient(
            api_key=key_id,
            secret_key=secret_key,
        )
        # tradeapi.REST(
        #     key_id=key_id, secret_key=secret_key, base_url=URL(TARGET_URL)
        # )
        BaseProvider.__init__(self)
        self._valid_assets: Set[str] = set()
        # for non supported APIs
        self._legacy_headers = {
            "content-type": "application/json",
            "Apca-Api-Key-Id": key_id,
            "Apca-Api-Secret-Key": secret_key,
        }

    @property
    def valid_assets(self) -> Set[str]:
        from alpaca.trading.client import GetAssetsRequest, Asset
        from alpaca.trading.requests import AssetClass

        if not self._valid_assets:
            self._valid_assets = {
                (x.symbol if isinstance(x, Asset) else x)
                for x in self.trading_client.get_all_assets(
                    GetAssetsRequest(
                        status=None, exchange=None, asset_class=AssetClass.US_EQUITY
                    )
                )
            }
        return self._valid_assets

    def _get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.requests import StockBarsRequest

        if at_day:
            today = datetime.now(tz=timezone.utc)
            start = min(
                datetime(at_day.year, at_day.month, at_day.day, tzinfo=timezone.utc),
                today - timedelta(days=7),
            )
            end = min(
                datetime.now(tz=timezone.utc) - timedelta(minutes=30),
                start + timedelta(days=7),
            )
            # end = datetime(at_day.year, at_day.month, at_day.day+7, hour=23, tzinfo=timezone.utc)
            raw = self.historical_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=tickers,
                    start=start,
                    end=end,
                    timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    limit=len(tickers) * 7,
                    adjustment=None,
                    feed=None,
                )
                # [ticker],
                # timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                # start=start.isoformat(),
                # end=end.isoformat(),
            )
            # take the first day after target day

            return {ticker: filter_prices_response(ticker, raw) for ticker in tickers}
        else:
            default = datetime.now(tz=timezone.utc) - timedelta(hours=1)
            start = default - timedelta(days=7)
            end = default
            raw = self.historical_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=tickers,
                    start=start,
                    end=end,
                    timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    feed=None,
                    adjustment=None,
                    limit=len(tickers) * 7,
                )
                # [ticker],
                # timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                # start=start.isoformat(),
                # end=end.isoformat(),
            )
            # take the first day after target day
            return {
                ticker: filter_prices_response(ticker, raw, earliest=False)
                for ticker in tickers
            }

    def _get_stock_info(self, ticker: str) -> dict:
        from alpaca.trading.client import Asset

        info = self.trading_client.get_asset(ticker)
        if not isinstance(info, Asset):
            return {}
        return {
            "name": info.name,
            "exchange": info.exchange,
            "tradable": bool(info.tradable),
        }

    def get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        batches = divide_into_batches(list(tickers), self.SUPPORTS_BATCH_HISTORY)
        final: Dict[str, Optional[Decimal]] = {}
        for batch in batches:
            final = {**final, **self._get_instrument_prices(batch, at_day=at_day)}
        return final

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
        from alpaca.common.exceptions import APIError

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
            try:
                raw = self.historical_client.get_stock_latest_quote(
                    StockLatestQuoteRequest(symbol_or_symbols=ticker, feed=None)
                )
                # if we don't have a value for the current day
                # expand out
                # this is required on weekends
                if raw[ticker].ask_price:
                    return Decimal(raw[ticker].ask_price)
            # if we didn't get something on this call, we can keep trying
            except APIError:
                pass
            default = datetime.now(tz=timezone.utc) - timedelta(hours=1)
            start = default - timedelta(days=7)
            end = default
            try:
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
                )
            except AttributeError:
                return None
            except APIError:
                return None
            # take the first day after target day
            return Decimal(raw[ticker][0].high)

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.common.exceptions import APIError

        if value:
            order_qty = None
        else:
            order_qty = qty
        market_order_data = MarketOrderRequest(
            symbol=ticker,
            notional=round(float(value), 2) if value else None,
            qty=float(order_qty) if order_qty else None,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            self.trading_client.submit_order(order_data=market_order_data)
        except APIError as e:
            import json

            error = json.loads(e._error)
            message = error.get("message", "Unknown Error")
            raise OrderError(message=f"Failed to buy {ticker} {qty} {e}: {message}")
        return True

    def _get_unsettled_cash(self):
        from alpaca.trading.requests import GetOrdersRequest, QueryOrderStatus

        open_orders = self.trading_client.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, limit=MAX_OPEN_ORDER_SIZE
            )
        )
        if len(open_orders) == MAX_OPEN_ORDER_SIZE:
            raise ValueError(
                "Returned max number of open orders - cannot continue safely"
            )
        return sum([Decimal(o.notional) for o in open_orders])

    def get_unsettled_instruments(self):
        from alpaca.trading.requests import GetOrdersRequest, QueryOrderStatus

        open_orders = self.trading_client.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, limit=MAX_OPEN_ORDER_SIZE
            )
        )
        if len(open_orders) == MAX_OPEN_ORDER_SIZE:
            raise ValueError(
                "Returned max number of open orders - cannot continue safely"
            )
        return set([o.symbol for o in open_orders])

    def get_holdings(self):
        from decimal import Decimal
        from alpaca.common.exceptions import APIError

        try:
            my_stocks = self.trading_client.get_all_positions()
            account = self.trading_client.get_account()
            unsettled = self.get_unsettled_instruments()
            unsettled_cash = self._get_unsettled_cash()
        except APIError as e:
            import json

            error = json.loads(e._error)
            message = error.get("message", None)
            if message == "forbidden":
                raise ConfigurationError("Account credentials invalid")
            raise e
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

        cash = Money(value=Decimal(account.cash) - unsettled_cash)

        if not my_stocks:
            return RealPortfolio(
                holdings=unsettled_elements,
                cash=cash,
                provider=self,
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
        return RealPortfolio(holdings=out, cash=cash, provider=self)

    def get_profit_or_loss(self) -> Money:
        my_stocks = self.trading_client.get_all_positions()
        _total_pl = sum([Decimal(value=o.unrealized_pl) for o in my_stocks])  # type: ignore
        return Money(value=_total_pl) + self._get_dividends()

    def _get_dividends(self):
        import requests
        import json

        api_call = "/v2/account/activities/DIV"
        headers = self._legacy_headers
        params = {
            "page_size": "100",
        }
        has_data = True
        all_data = []
        while has_data:
            response = requests.get(
                self.LEGACY_BASE + api_call, params=params, headers=headers
            )
            response = json.loads(response.text)
            all_data += response

            if len(response) == 0:
                has_data = False
            else:
                params["page_token"] = response[-1]["id"]
        return Money(value=sum([Decimal(x["net_amount"]) for x in all_data]))


class PaperAlpacaProvider(AlpacaProvider):
    PROVIDER = Provider.ALPACA_PAPER
    API_KEY_VARIABLE = "ALPACA_PAPER_API_KEY"
    API_SECRET_VARIABLE = "ALPACA_PAPER_API_SECRET"

    LEGACY_BASE = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
    ):
        super().__init__(key_id=key_id, secret_key=secret_key, paper=True)
