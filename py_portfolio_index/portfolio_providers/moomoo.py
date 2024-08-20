from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List, Dict, DefaultDict, Any
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
    ProfitModel,
)
from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    CacheKey,
)
from py_portfolio_index.exceptions import (
    ConfigurationError,
    PriceFetchError,
    OrderError,
)
from py_portfolio_index.constants import CACHE_DIR
from py_portfolio_index.enums import Provider
from functools import lru_cache
from os import environ
from collections import defaultdict

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50

CACHE_PATH = "moo_moo_tickers.json"


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


def nearest_multi_value(
    symbol: str, all_historicals, pivot: Optional[date] = None
) -> Optional[Decimal]:
    filtered = [z for z in all_historicals if z and z["symbol"] == symbol]
    if not filtered:
        return None
    if pivot is not None:
        lpivot = pivot or date.today()
        closest = min(
            filtered,
            key=lambda x: abs(
                datetime.strptime(x["begins_at"], "%Y-%m-%dT%H:%M:%SZ").date() - lpivot
            ),
        )
    else:
        closest = filtered[0]
    if closest:
        value = closest.get("last_trade_price", closest.get("high_price", None))
        return Decimal(value)
    return None


class InstrumentDict(dict):
    def __init__(self, refresher, *args):
        super().__init__(*args)
        self.refresher = refresher

    def __missing__(self, key):
        mapping = self.refresher()
        self.update(mapping)
        if key in self:
            return self[key]
        raise ValueError(f"Could not find instrument {key} after refresh")


class MooMooProvider(BaseProvider):
    """Provider for interacting with stocks held in
    MooMoo
    """

    PROVIDER = Provider.MOOMOO
    SUPPORTS_BATCH_HISTORY = 0
    PASSWORD_ENV = "MOOMOO_PASSWORD"
    USERNAME_ENV = "MOOMOO_USERNAME"
    TRADE_TOKEN_ENV = "MOOMOO_TRADE_TOKEN"
    DEVICE_ID_ENV = "MOOMOO_DEVICE_ID"

    def __init__(
        self,
        skip_cache: bool = False,
    ):
        from moomoo import (
            OpenSecTradeContext,
            OpenQuoteContext,
            SecurityFirm,
            TrdMarket,
        )

        self._trade_provider = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.US,
            host="localhost",
            port=11111,
            security_firm=SecurityFirm.FUTUINC,
        )
        self._quote_provider = OpenQuoteContext(host="localhost", port=11111)
        BaseProvider.__init__(self)
        self._local_latest_price_cache: Dict[str, Decimal | None] = defaultdict(
            lambda: None
        )
        self._local_instrument_cache: Dict[str, str] = {}
        if not skip_cache:
            self._load_local_instrument_cache()

    def _load_local_instrument_cache(self):
        from platformdirs import user_cache_dir
        from pathlib import Path
        import json

        path = Path(user_cache_dir(CACHE_DIR, ensure_exists=True))
        file = path / CACHE_PATH
        if not file.exists():
            self._local_instrument_cache = {}
            return
        with open(file, "r") as f:
            self._local_instrument_cache = json.load(f)
            # corruption guard
            if not isinstance(self._local_instrument_cache, dict):
                self._local_instrument_cache = {}

    def _save_local_instrument_cache(self):
        from platformdirs import user_cache_dir
        from pathlib import Path
        import json

        path = Path(user_cache_dir(CACHE_DIR, ensure_exists=True))
        file = path / CACHE_PATH
        with open(file, "w") as f:
            json.dump(self._local_instrument_cache, f)

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
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
            ret_sub, err_message = self._quote_provider.subscribe(
                ["US." + ticker], [SubType.TICKER], subscribe_push=False
            )
            # Subscribe to the K line type first. After the subscription is successful, moomoo OpenD will continue to receive pushes from the server, False means that there is no need to push to the script temporarily
            if ret_sub == RET_OK:  # Subscription successful
                ret, data = self._quote_provider.get_stock_quote(
                    ["US." + ticker]
                )  # Get real-time data of subscription stock quotes
                if ret == RET_OK:
                    return list(data.itertuples())[0]
            raise PriceFetchError(
                [ticker], f"Subscription failed, could not get price: {err_message}"
            )

    def _buy_instrument(
        self,
        symbol: str,
        qty: Optional[float],
        value: Optional[Money] = None,
        price: Optional[Decimal] = None,
    ) -> bool:
        from moomoo import RET_OK, TrdSide, OrderType

        ret, data = self._trade_provider.unlock_trade(
            environ.get(self.TRADE_TOKEN_ENV, None)
        )  # If you use a live trading account to place an order, you need to unlock the account first. The example here is to place an order on a paper trading account, and unlocking is not necessary.
        if ret == RET_OK:
            pass
        else:
            raise OrderError("unlock trade error: ", data)
        ret, data = self._trade_provider.place_order(
            price=price,
            qty=qty,
            code="US." + symbol,
            order_type=OrderType.MARKET,
            trd_side=TrdSide.BUY,
        )
        if ret == RET_OK:
            return True
        else:
            raise OrderError("place_order error: ", data)

    def buy_instrument(
        self, ticker: str, qty: Decimal, value: Optional[Money] = None
    ) -> bool:
        # TODO: make sure this is always set
        if not value and qty:
            raise NotImplementedError(
                "Moomoo provider must have both value and qty to purchase"
            )
        assert value
        price = value / qty
        if qty:
            orders_kwargs_list: List[Dict[str, Money | None | Decimal]] = [
                {"qty": qty, "value": None, "price": price}
            ]
        else:
            orders_kwargs_list = [{"qty": None, "value": value, "price": price}]
        for order_kwargs in orders_kwargs_list:
            return self._buy_instrument(ticker, **order_kwargs)  # type: ignore
        return True

    def get_unsettled_instruments(self) -> set[str]:
        """We need to efficiently bypass
        paginating all orders if possible
        so just check the account info for if there
        is any cash held for orders first"""
        from moomoo import RET_OK
        from moomoo import OrderStatus

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
        return set(item.code.split(".")[-1] for item in data.itertuples())

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
            return list(data.itertuples())[0]

        raise ConfigurationError("Could not get portfolio")

    def _get_positions(self):
        from moomoo import RET_OK

        ret, data = self._trade_provider.position_list_query()
        if ret == RET_OK:
            return data.itertuples()

        raise ConfigurationError("Could not get positions")

    def get_holdings(self) -> RealPortfolio:
        accounts_data = self._get_cached_value(
            CacheKey.ACCOUNT, callable=self._get_portfolio
        )
        my_stocks = self._get_cached_value(
            CacheKey.POSITIONS, callable=self._get_positions
        )
        unsettled = self._get_cached_value(
            CacheKey.UNSETTLED, callable=self.get_unsettled_instruments
        )

        pre = {}
        symbols = []
        total_value = Decimal(0.0)
        for row in my_stocks:
            local: Dict[str, Any] = {}
            local["units"] = row.qty
            # instrument_data = self._provider.get_instrument_by_url(row["instrument"])
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
        cash = Decimal(accounts_data.net_cash_power)
        return RealPortfolio(holdings=out, cash=Money(value=cash), provider=self)

    def get_instrument_prices(self, tickers: List[str], at_day: Optional[date] = None):
        return self._price_cache.get_prices(tickers=tickers, date=at_day)

    def _get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        batches: List[Dict[str, Optional[Decimal]]] = []
        for list_batch in divide_into_batches(tickers, 1):
            # TODO: determine if there is a bulk API
            ticker: str = list_batch[0]
            # webull_id = self._local_instrument_cache.get(ticker)
            rval = self._get_instrument_price(ticker, at_day=at_day)
            batches.append({ticker: rval})
        prices: Dict[str, Optional[Decimal]] = {}
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices

    def get_profit_or_loss(self) -> ProfitModel:
        raise NotImplementedError()
        # my_stocks = self._get_cached_value(
        #     CacheKey.POSITIONS, callable=self._provider.get_positions
        # )
        # pls: List[Money] = []
        # for x in my_stocks:
        #     pl = Money(value=Decimal(x["unrealizedProfitLoss"]))
        #     pls.append(pl)
        # _total_pl = sum(pls)  # type: ignore
        # if not include_dividends:
        #     return Money(value=_total_pl)
        # return Money(value=_total_pl) + sum(self._get_dividends().values())

    def _get_dividends(self) -> DefaultDict[str, Money]:
        # dividends = self._provider.get_dividends()
        out: DefaultDict[str, Money] = DefaultDict(lambda: Money(value=0))
        return out


# class MooMooPaperProvider(MooMooProvider):
#     PROVIDER = Provider.MOOMOO_PAPER
#     PASSWORD_ENV = "MOOMOO_PAPER_PASSWORD"
#     USERNAME_ENV = "MOOMOO_PAPER_USERNAME"
#     TRADE_TOKEN_ENV = "MOOMOO_PAPER_TRADE_TOKEN"
#     DEVICE_ID_ENV = "MOOMOO_PAPER_DEVICE_ID"

#     def _get_provider(self):
#         from webull import paper_webull

#         return paper_webull
