from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
    ProfitModel,
)
from py_portfolio_index.exceptions import ConfigurationError, OrderError
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    CachedValue,
    ObjectKey,
)
from decimal import Decimal
from typing import Optional, Dict, List, Set, DefaultDict
from datetime import date, datetime, timezone, timedelta
from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.models import DividendResult
from os import environ
from py_portfolio_index.portfolio_providers.common import PriceCache
from collections import defaultdict
import requests
import json

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
        if x.close:
            return Decimal(x.close)
    return None


class AlpacaProvider(BaseProvider):
    SUPPORTS_BATCH_HISTORY = 50
    PROVIDER = ProviderType.ALPACA

    API_KEY_VARIABLE = "ALPACA_API_KEY"
    API_SECRET_VARIABLE = "ALPACA_API_SECRET"

    LEGACY_BASE = "https://api.alpaca.markets"

    CACHE: dict[str, CachedValue] = {}

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
        self._price_cache: PriceCache = PriceCache(
            fetcher=self._get_instrument_prices_wrapper,
            single_fetcher=self._get_instrument_price,
        )

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
        from alpaca.data.requests import StockBarsRequest, Adjustment

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
            raw = self.historical_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=tickers,
                    start=start,
                    end=end,
                    timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
                    limit=len(tickers) * 7,
                    adjustment=Adjustment.SPLIT,
                    feed=None,
                )
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

    def _get_instrument_prices_wrapper(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        batches = divide_into_batches(list(tickers), self.SUPPORTS_BATCH_HISTORY)
        final: Dict[str, Optional[Decimal]] = {}
        for batch in batches:
            final = {**final, **self._get_instrument_prices(batch, at_day=at_day)}
        return final

    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        return self._price_cache.get_prices(tickers=[ticker], date=at_day)[ticker]

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

    def sell_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
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
            qty=order_qty if order_qty else None,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            self.trading_client.submit_order(order_data=market_order_data)
        except APIError as e:
            import json

            error = json.loads(e._error)
            message = error.get("message", "Unknown Error")
            raise OrderError(message=f"Failed to sell {ticker} {qty} {e}: {message}")
        return True

    def _get_unsettled_cash(self):
        from alpaca.trading.requests import GetOrdersRequest, QueryOrderStatus

        open_orders = self._get_cached_value(
            ObjectKey.OPEN_ORDERS,
            callable=lambda: self.trading_client.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, limit=MAX_OPEN_ORDER_SIZE
                )
            ),
        )
        if len(open_orders) == MAX_OPEN_ORDER_SIZE:
            raise ValueError(
                "Returned max number of open orders - cannot continue safely"
            )
        return sum([Decimal(o.notional) for o in open_orders])

    def get_unsettled_instruments(self):
        from alpaca.trading.requests import GetOrdersRequest, QueryOrderStatus

        open_orders = self._get_cached_value(
            ObjectKey.OPEN_ORDERS,
            callable=lambda: self.trading_client.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, limit=MAX_OPEN_ORDER_SIZE
                )
            ),
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
            my_stocks = self._get_cached_value(
                ObjectKey.POSITIONS, callable=self.trading_client.get_all_positions
            )
            account = self._get_cached_value(
                ObjectKey.ACCOUNT, callable=self.trading_client.get_account
            )
            unsettled = self._get_cached_value(
                ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments
            )
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
        total_value = sum(
            [Decimal(item.market_value) for item in my_stocks if item.market_value]
        )

        profit = self.get_per_ticker_profit_or_loss()
        out = [
            RealPortfolioElement(
                ticker=row.symbol,
                units=row.qty,
                value=Money(value=Decimal(row.market_value if row.market_value else 0)),
                weight=Decimal(row.market_value if row.market_value else 0)
                / total_value,
                unsettled=row.symbol in unsettled,
                appreciation=profit[row.symbol].appreciation,
                dividends=profit[row.symbol].dividends,
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

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
        my_stocks = self._get_cached_value(
            ObjectKey.POSITIONS, callable=self.trading_client.get_all_positions
        )
        raw_divs = [x for x in self._get_dividends() if x["status"] == "executed"]

        divs: DefaultDict[str, Money] = defaultdict(lambda: Money(value=Decimal(0)))
        for z in raw_divs:
            divs[z["symbol"]] += Money(value=Decimal(z["net_amount"]))
        base = {
            o.symbol: ProfitModel(
                appreciation=Money(value=o.unrealized_pl if o.unrealized_pl else 0),
                dividends=divs.get(o.symbol, Money(value=Decimal(0))),
            )
            for o in my_stocks
        }
        for x in raw_divs:
            if x["symbol"] not in base:
                base[x["symbol"]] = ProfitModel(
                    appreciation=Money(value=Decimal(0)),
                    dividends=Money(value=Decimal(x["net_amount"])),
                )
        return base

    def _get_dividends(self) -> list[dict]:
        api_call = "/v2/account/activities/DIV"
        headers = self._legacy_headers
        params = {
            "page_size": "100",
        }
        all_data = []
        has_data = True
        while has_data:
            raw_response = requests.get(
                self.LEGACY_BASE + api_call, params=params, headers=headers
            )
            response = json.loads(raw_response.text)
            all_data += response

            if len(response) == 0:
                has_data = False
            else:
                params["page_token"] = response[-1]["id"]

        return all_data

    def get_dividend_details(
        self, start: datetime | None = None
    ) -> list[DividendResult]:
        value = self._get_dividends()
        final = []
        for x in value:
            if x["status"] == "executed":
                paid_date = date.fromisoformat(x["date"])
                if start and paid_date < start.date():
                    continue
                final.append(
                    DividendResult(
                        ticker=x["symbol"],
                        amount=Money(value=float(x["net_amount"])),
                        date=paid_date,
                        provider=self.PROVIDER,
                    )
                )
        return final


class PaperAlpacaProvider(AlpacaProvider):
    PROVIDER = ProviderType.ALPACA_PAPER
    API_KEY_VARIABLE = "ALPACA_PAPER_API_KEY"
    API_SECRET_VARIABLE = "ALPACA_PAPER_API_SECRET"

    LEGACY_BASE = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
    ):
        super().__init__(key_id=key_id, secret_key=secret_key, paper=True)
