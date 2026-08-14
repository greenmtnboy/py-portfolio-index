import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from decimal import Decimal
from os import environ
from typing import Any

from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.exceptions import (
    ConfigurationError,
    OrderError,
    PriceFetchError,
)
from py_portfolio_index.models import (
    Money,
    ProfitModel,
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    ObjectKey,
)
from py_portfolio_index.portfolio_providers.common import instance_cache
from py_portfolio_index.portfolio_providers.helpers.moomoo import (
    DEFAULT_PORT,
    MooMooProxy,
)

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50

CACHE_PATH = "moo_moo_tickers.json"


class MooMooProvider(BaseProvider):
    """Provider for interacting with stocks held in
    MooMoo
    """

    PROVIDER = ProviderType.MOOMOO
    SUPPORTS_BATCH_HISTORY = 0
    SUPPORTS_FRACTIONAL_SHARES = False
    PASSWORD_ENV = "MOOMOO_PASSWORD"
    ACCOUNT_ENV = "MOOMOO_ACCOUNT"
    TRADE_TOKEN_ENV = "MOOMOO_TRADE_TOKEN"
    DEVICE_ID_ENV = "MOOMOO_DEVICE_ID"
    OPEND_ENV = "MOOMOO_OPEND_PATH"

    Proxy = MooMooProxy

    def __init__(
        self,
        proxy: MooMooProxy,
        account: str | None = None,
        password: str | None = None,
        trade_token: str | None = None,
        quote_provider: BaseProvider | None = None,
        _external_auth: bool = False,
        raise_on_rate_limit: bool = False,
    ):
        from moomoo import (
            OpenQuoteContext,
            OpenSecTradeContext,
            SecurityFirm,
            TrdMarket,
        )

        if not account:
            account = environ.get(self.ACCOUNT_ENV, None)
        if not password:
            password = environ.get(self.PASSWORD_ENV, None)
        if not trade_token:
            trade_token = environ.get(self.TRADE_TOKEN_ENV, "ABC")
        self._trade_token = trade_token
        # if not device_id:
        #     device_id = environ.get(self.DEVICE_ID_ENV, None)
        if not (account and password and trade_token) and not _external_auth:
            raise ConfigurationError("Must provide ALL OF account, password, trade_token, and arguments or set environment variables MOOMOO_ACCOUNT, MOOMOO_PASSWORD, MOOMOO_TRADE_TOKEN")
        self.proxy = proxy
        self.proxy.validate(account=account, pwd=password)
        self._trade_provider = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.US,
            host="localhost",
            port=DEFAULT_PORT,
            security_firm=SecurityFirm.FUTUINC,
        )
        self._quote_context = OpenQuoteContext(host="localhost", port=DEFAULT_PORT)
        BaseProvider.__init__(self, quote_provider=quote_provider)
        self._local_latest_price_cache: dict[str, Decimal | None] = defaultdict(lambda: None)
        self.last_unlocked: datetime | None = None

        # Rate limiting for orders: max 15 orders per 30 seconds
        self._order_timestamps: deque = deque()
        self._max_orders_per_window = 14  # Conservative: 14 instead of 15
        self._rate_limit_window = 30  # seconds
        self._raise_on_rate_limit = raise_on_rate_limit

    def _check_order_rate_limit(self) -> None:
        """Check if we can place an order without exceeding rate limits.
        Either raises OrderError or sleeps to throttle based on raise_on_rate_limit flag.
        """
        current_time = time.time()

        # Remove timestamps older than the rate limit window
        while self._order_timestamps and current_time - self._order_timestamps[0] > self._rate_limit_window:
            self._order_timestamps.popleft()

        # Check if we would exceed the rate limit
        if len(self._order_timestamps) >= self._max_orders_per_window:
            oldest_timestamp = self._order_timestamps[0]
            wait_time = self._rate_limit_window - (current_time - oldest_timestamp)

            if self._raise_on_rate_limit:
                raise OrderError(
                    f"Order rate limit exceeded. Please wait {wait_time:.1f} seconds before placing another order. "
                    f"Current limit: {self._max_orders_per_window} orders per {self._rate_limit_window} seconds."
                )
            else:
                # Sleep to throttle the order submission
                # Add a small buffer (0.1 seconds) to ensure we're safely under the limit
                sleep_time = wait_time + 0.1
                time.sleep(sleep_time)

    def _record_order_attempt(self) -> None:
        """Record that an order attempt was made."""
        self._order_timestamps.append(time.time())

    @instance_cache
    def _get_instrument_price(self, ticker: str, at_day: date | None = None, fail_on_missing: bool = True) -> Decimal | None:
        # TODO: determine if there is a bulk API
        from moomoo import RET_OK, SubType

        if at_day:
            raise NotImplementedError
            # historicals = self._provider.get_bars(
            #     tId=webull_id,
            #     interval="d1",
            #     timeStamp=int(
            #         datetime(
            #             day=at_day.day,
            #             month=at_day.month,
            #             year=at_day.year,
            #             tzinfo=UTC,
            #         ).timestamp()
            #     ),
            # )
            # return Decimal(value=list(historicals.itertuples())[0].vwap)
        else:
            ret_sub, err_message = self._quote_context.subscribe(["US." + ticker], [SubType.TICKER], subscribe_push=False)
            # Subscribe to the K line type first. After the subscription is successful, moomoo OpenD will continue to receive pushes from the server, False means that there is no need to push to the script temporarily
            if ret_sub == RET_OK:  # Subscription successful
                ret, data = self._quote_context.get_stock_quote(["US." + ticker])  # Get real-time data of subscription stock quotes
                if ret == RET_OK:
                    return next(iter(data.itertuples()))
            raise PriceFetchError([ticker], f"Subscription failed, could not get price: {err_message}")

    def _buy_instrument(
        self,
        symbol: str,
        qty: float | None,
        value: Money | None = None,
    ) -> bool:
        from moomoo import RET_OK, OrderType, TrdSide

        # Check rate limit before attempting to place order
        self._check_order_rate_limit()

        if not self.last_unlocked or (datetime.now(timezone.utc) - self.last_unlocked).seconds > 300:
            ret, data = self._trade_provider.unlock_trade(password=self._trade_token)
            if ret == RET_OK:
                self.last_unlocked = datetime.now(timezone.utc)
            else:
                raise ConfigurationError(f"unlock trade error: {data}")

        # Record the order attempt before making the API call
        self._record_order_attempt()

        ret, data = self._trade_provider.place_order(
            # price is arbitrary for makret
            price=0.0 if not value else value.value,
            qty=qty,
            code="US." + symbol,
            order_type=OrderType.MARKET,
            trd_side=TrdSide.BUY,
        )
        if ret == RET_OK:
            return True
        else:
            raise OrderError(f"place_order error: {data} debug: {qty} {value}")

    def buy_instrument(self, ticker: str, qty: Decimal, value: Money | None = None) -> bool:
        if qty:
            orders_kwargs_list: list[dict[str, Money | None | Decimal]] = [
                {
                    "qty": qty,
                    "value": None,
                }
            ]
        elif value:
            # market orders require quantity not price
            # so convert here
            price = self.get_instrument_price(ticker)
            orders_kwargs_list = [{"qty": value / price, "value": None}]
        else:
            raise OrderError("Must provide either qty or value for order")
        for order_kwargs in orders_kwargs_list:
            return self._buy_instrument(ticker, **order_kwargs)  # type: ignore
        return True

    def get_unsettled_instruments(self) -> set[str]:
        """We need to efficiently bypass
        paginating all orders if possible
        so just check the account info for if there
        is any cash held for orders first"""
        from moomoo import RET_OK, OrderStatus

        ret, data = self._trade_provider.order_list_query(
            status_filter_list=[
                OrderStatus.SUBMITTED,
                OrderStatus.FILLED_PART,
                OrderStatus.WAITING_SUBMIT,
            ]
        )
        if ret == RET_OK:
            pass
        else:
            raise ConfigurationError("Could not get order list")
        # code is of format US.MSFT, for example
        return {item.code.split(".")[-1] for item in data.itertuples()}

    def _get_stock_info(self, ticker: str) -> dict:
        raise NotImplementedError()
        # info = self._provider.get_ticker_info(ticker)
        # matches = self._provider.find_instrument_data(ticker)
        # for match in matches:
        #     if match["symbol"] == ticker:
        #         return {
        #             "name": match["simple_name"],
        #             "exchange": match["exchange"],
        #             "market": match["market"],
        #             "country": match["country"],
        #             "tradable": bool(match["tradable"]),
        #         }
        # return info

    def _get_portfolio(self):
        from moomoo import RET_OK

        ret, data = self._trade_provider.accinfo_query()
        if ret == RET_OK:
            return next(iter(data.itertuples()))

        raise ConfigurationError("Could not get portfolio")

    def _get_positions(self):
        from moomoo import RET_OK

        ret, data = self._trade_provider.position_list_query()
        if ret == RET_OK:
            return list(data.itertuples())

        raise ConfigurationError("Could not get positions")

    def get_holdings(self) -> RealPortfolio:
        accounts_data = self._get_cached_value(ObjectKey.ACCOUNT, callable=self._get_portfolio)
        my_stocks = self._get_cached_value(ObjectKey.POSITIONS, callable=self._get_positions)

        unsettled = self._get_cached_value(ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments)

        pre = {}
        symbols = []
        total_value = Decimal("0.0")
        for row in my_stocks:
            local: dict[str, Any] = {}
            local["units"] = row.qty
            ticker = row.code.split(".")[-1]
            local["ticker"] = ticker
            symbols.append(ticker)
            local["value"] = Decimal(row.market_val)
            local["weight"] = 0
            pre[ticker] = local
            total_value += local["value"]

        final = []
        for s in symbols:
            local = pre[s]
            local["weight"] = local["value"] / total_value
            local["unsettled"] = s in unsettled
            final.append(local)
        out = [RealPortfolioElement(**row) for row in final]
        # THIS IS US SPECIFIC ATM
        cash = Decimal(accounts_data.us_cash)
        return RealPortfolio(
            holdings=out,
            cash=Money(value=cash),
            provider=self,
            profit_and_loss=self.get_profit_or_loss(),
        )

    def _get_instrument_prices(
        self,
        tickers: list[str],
        at_day: date | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal | None]:
        batches: list[dict[str, Decimal | None]] = []
        for list_batch in divide_into_batches(tickers, 1):
            # TODO: determine if there is a bulk API
            ticker: str = list_batch[0]
            rval = self._get_instrument_price(ticker, at_day=at_day)
            batches.append({ticker: rval})
        prices: dict[str, Decimal | None] = {}
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices

    def get_per_ticker_profit_or_loss(self) -> dict[str, ProfitModel]:
        my_stocks = self._get_cached_value(ObjectKey.POSITIONS, callable=self._get_positions)
        dividends = self._get_cached_value(ObjectKey.DIVIDENDS, callable=self._get_dividends)
        output = {}
        for x in my_stocks:
            ticker = x.code.split(".")[-1]
            output[ticker] = ProfitModel(appreciation=Money(value=Decimal(x.pl_val)), dividends=dividends[ticker])
        return output

    def _get_dividends(self) -> defaultdict[str, Money]:
        final: defaultdict[str, Money] = defaultdict(lambda: Money(value=0))
        return final

    def get_dividend_history(self) -> dict[str, Money]:
        return super().get_dividend_history()

    def get_dividend_details(self, start=None):
        # TODO: get endpoint for this
        # https://github.com/MoomooOpen/py-moomoo-api/issues/12
        return []

    def _shutdown(self):
        self._trade_provider.close()
        self._quote_context.close()
        if self.proxy:
            self.proxy.close()
