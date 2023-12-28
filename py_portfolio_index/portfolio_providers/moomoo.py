from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List, Dict, DefaultDict, Any
from py_portfolio_index.constants import Logger
from py_portfolio_index.models import RealPortfolio, RealPortfolioElement, Money
from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.portfolio_providers.common import PriceCache
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    CacheKey,
)
from py_portfolio_index.exceptions import ConfigurationError, PriceFetchError
import uuid
from py_portfolio_index.enums import Provider
from functools import lru_cache
from os import environ
from pytz import UTC

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
        # if not username:
        #     username = environ.get(self.USERNAME_ENV, None)
        # if not password:
        #     password = environ.get(self.PASSWORD_ENV, None)
        # if not trade_token:
        #     trade_token = environ.get(self.TRADE_TOKEN_ENV, None)
        # if not device_id:
        #     device_id = environ.get(self.DEVICE_ID_ENV, None)
        # if not (username and password and trade_token and device_id):
        #     raise ConfigurationError(
        #         "Must provide ALL OF username, password, trade_token, and device_id arguments or set environment variables MOOMOO_USERNAME, MOOMOO_PASSWORD, MOOMOO_TRADE_TOKEN, and MOOMOO_DEVICE_ID "
        #     )
        from moomoo import OpenSecTradeContext, OpenQuoteContext, SecurityFirm, TrdMarket
        self._trade_provider = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host='localhost', port=11111,security_firm=SecurityFirm.FUTUINC)
        self._quote_provider = OpenQuoteContext(host='localhost', port=11111)
        BaseProvider.__init__(self)
        self._local_latest_price_cache: Dict[str, Decimal] = {}
        self._price_cache: PriceCache = PriceCache(fetcher=self._get_instrument_prices)
        self._local_instrument_cache: Dict[str,str] = {}
        if not skip_cache:
            self._load_local_instrument_cache()

            

    def _load_local_instrument_cache(self):
        from platformdirs import user_cache_dir
        from pathlib import Path
        import json

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
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

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
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
            raise ValueError
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
            ret_sub, err_message = self._quote_provider.subscribe(['US.'+ticker], [SubType.QUOTE], subscribe_push=False)
            # Subscribe to the K line type first. After the subscription is successful, moomoo OpenD will continue to receive pushes from the server, False means that there is no need to push to the script temporarily
            if ret_sub == RET_OK: # Subscription successful
                ret, data = self._quote_provider.get_stock_quote(['US.'+ticker]) # Get real-time data of subscription stock quotes
                if ret == RET_OK:
                    return list(data.itertuples())[0]
            else:
                print('subscription failed', err_message)
            raise PriceFetchError("Could not get price")

    def _buy_instrument(
        self, symbol: str, qty: Optional[float], value: Optional[Money] = None
    ) -> dict:
        from webull import webull
        import requests

        # we should always have this at this point, as we would have had
        # to check price
        rtId:Optional[str] = self._local_instrument_cache.get(symbol)
        if not rtId:
            tId = self._provider.get_ticker(symbol)
            self._local_instrument_cache[symbol] = tId
            self._save_local_instrument_cache()
        else:
            tId = rtId


        def place_order(
            provider: webull,
            tId=tId,
            price=value,
            action="BUY",
            orderType="LMT",
            enforce="GTC",
            quant = qty,
            outsideRegularTradingHour=True,
            stpPrice=None,
            trial_value=0,
            trial_type="DOLLAR",
        ):
            """
            Place an order - redefined here to

            price: float (LMT / STP LMT Only)
            action: BUY / SELL / SHORT
            ordertype : LMT / MKT / STP / STP LMT / STP TRAIL
            timeinforce:  GTC / DAY / IOC
            outsideRegularTradingHour: True / False
            stpPrice: float (STP / STP LMT Only)
            trial_value: float (STP TRIAL Only)
            trial_type: DOLLAR / PERCENTAGE (STP TRIAL Only)
            """

            headers = provider.build_req_headers(
                include_trade_token=True, include_time=True
            )
            data = {
                "action": action,
                "comboType": "NORMAL",
                "orderType": orderType,
                "outsideRegularTradingHour": outsideRegularTradingHour,
                "quantity": quant if orderType == "MKT" else int(quant),
                "serialId": str(uuid.uuid4()),
                "tickerId": tId,
                "timeInForce": enforce,
            }

            # Market orders do not support extended hours trading.
            if orderType == "MKT":
                data["outsideRegularTradingHour"] = False
            elif orderType == "LMT":
                data["lmtPrice"] = float(price)
            elif orderType == "STP":
                data["auxPrice"] = float(stpPrice)
            elif orderType == "STP LMT":
                data["lmtPrice"] = float(price)
                data["auxPrice"] = float(stpPrice)
            elif orderType == "STP TRAIL":
                data["trailingStopStep"] = float(trial_value)
                data["trailingType"] = str(trial_type)
            response = requests.post(
                provider._urls.place_orders(provider._account_id),
                json=data,
                headers=headers,
                timeout=provider.timeout,
            )
            return response.json()

        return place_order(
            self._provider,
            action="BUY",
            price=value,
            quant=qty,
            orderType="MKT",
            enforce="DAY",
        )

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        if qty:
            float_qty = float(qty)
            import math

            if float_qty > 1:
                remainder_part, int_part = math.modf(float_qty)

                orders = [int(int_part), round(remainder_part, 4)]
            else:
                orders = [float_qty]
            for order in orders:
                output = self._buy_instrument(ticker, qty=order, value=None)
                msg = output.get("msg")
                if not output.get("success"):
                    if msg:
                        Logger.error(msg)
                        raise ValueError(msg)
                    Logger.error(output)
                    raise ValueError(output)
        else:
            output = self._buy_instrument(ticker, qty=None, value=value)
            msg = output.get("msg")
            if not output.get("success"):
                if msg:
                    Logger.error(msg)
                    raise ValueError(msg)
                Logger.error(output)
                raise ValueError(output)
        return True

    def get_unsettled_instruments(self) -> set[str]:
        """We need to efficiently bypass
        paginating all orders if possible
        so just check the account info for if there
        is any cash held for orders first"""
        from moomoo import RET_OK
        ret, data = self._trade_provider.order_list_query()
        if ret == RET_OK:
            pass
        else:
            raise ConfigurationError("Could not get order list")
        return set(item.symbol for item in data.itertuples())

    def _get_stock_info(self, ticker: str) -> dict:
        info = self._provider.get_ticker_info(ticker)
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
        return info
    
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


    def get_holdings(self)->RealPortfolio:
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
        for row in my_stocks:
            local: Dict[str, Any] = {}
            local["units"] = row["position"]
            # instrument_data = self._provider.get_instrument_by_url(row["instrument"])
            ticker = row["ticker"]["symbol"]
            local["ticker"] = ticker
            symbols.append(ticker)
            local["value"] = 0
            local["weight"] = 0
            pre[ticker] = local
        prices = self.get_instrument_prices(symbols)
        self._local_latest_price_cache = {**prices, **self._local_latest_price_cache}
        total_value = Decimal(0.0)
        for s in symbols:
            if not self._local_latest_price_cache[s]:
                continue
            total_value += self._local_latest_price_cache[s] * Decimal(pre[s]["units"])
        final = []
        for s in symbols:
            local = pre[s]
            value = Decimal(self._local_latest_price_cache[s] or 0) * Decimal(
                pre[s]["units"]
            )
            local["value"] = Money(value=value)
            local["weight"] = value / total_value
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

    def get_profit_or_loss(self, include_dividends: bool = True) -> Money:
        my_stocks = self._get_cached_value(
            CacheKey.POSITIONS, callable=self._provider.get_positions
        )
        pls: List[Money] = []
        for x in my_stocks:
            pl = Money(value=Decimal(x["unrealizedProfitLoss"]))
            pls.append(pl)
        _total_pl = sum(pls)  # type: ignore
        if not include_dividends:
            return Money(value=_total_pl)
        return Money(value=_total_pl) + sum(self._get_dividends().values())

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
