from decimal import Decimal
from datetime import date, datetime
from typing import Optional, List, Dict, DefaultDict, Any
from py_portfolio_index.constants import Logger, CACHE_DIR
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
    ProfitModel,
)
from py_portfolio_index.common import divide_into_batches

from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    ObjectKey,
)
from py_portfolio_index.exceptions import ConfigurationError, PriceFetchError
from py_portfolio_index.models import DividendResult
from collections import defaultdict
import uuid
from py_portfolio_index.enums import ProviderType
from functools import lru_cache
from os import environ
from pytz import UTC

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50

DEFAULT_WEBULL_TIMEOUT = 60
CACHE_PATH = "webull_tickers.json"


class WebullProvider(BaseProvider):
    """Provider for interacting with stocks held in
    Webull
    """

    PROVIDER = ProviderType.WEBULL
    SUPPORTS_BATCH_HISTORY = 0
    PASSWORD_ENV = "WEBULL_PASSWORD"
    USERNAME_ENV = "WEBULL_USERNAME"
    TRADE_TOKEN_ENV = "WEBULL_TRADE_TOKEN"
    DEVICE_ID_ENV = "WEBULL_DEVICE_ID"

    def _get_provider(self):
        from webull import webull  # for paper trading, import 'paper_webull'

        return webull

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        trade_token: str | None = None,
        device_id: str | None = None,
        skip_cache: bool = False,
    ):
        if not username:
            username = environ.get(self.USERNAME_ENV, None)
        if not password:
            password = environ.get(self.PASSWORD_ENV, None)
        if not trade_token:
            trade_token = environ.get(self.TRADE_TOKEN_ENV, None)
        if not device_id:
            device_id = environ.get(self.DEVICE_ID_ENV, None)
        if not (username and password and trade_token and device_id):
            raise ConfigurationError(
                "Must provide ALL OF username, password, trade_token, and device_id arguments or set environment variables WEBULL_USERNAME, WEBULL_PASSWORD, WEBULL_TRADE_TOKEN, and WEBULL_DEVICE_ID "
            )
        webull = self._get_provider()
        self._provider = webull()
        self._provider.timeout = 60
        # we must set both of these to have a valid login
        self._provider._did = device_id
        self._provider._headers["did"] = device_id

        self._provider.login(username=username, password=password)
        self._local_instrument_cache: Dict[str, str] = {}
        if not skip_cache:
            self._load_local_instrument_cache()

        token = self._provider.get_trade_token(trade_token)
        if not token:
            raise ConfigurationError("Could not get trade token with provided password")
        account_info: dict = self._provider.get_account()
        if account_info.get("success") is False:
            raise ConfigurationError(f"Authentication is expired: {account_info}")
        BaseProvider.__init__(self)

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
        webull_id = self._local_instrument_cache.get(ticker)
        if not webull_id:
            # skip the call
            lookup_ticker = ticker.replace(".", "-")
            webull_id = str(self._provider.get_ticker(lookup_ticker))
            self._local_instrument_cache[ticker] = webull_id
            self._save_local_instrument_cache()
        if at_day:
            historicals = self._provider.get_bars(
                tId=webull_id,
                interval="d1",
                timeStamp=int(
                    datetime(
                        day=at_day.day,
                        month=at_day.month,
                        year=at_day.year,
                        tzinfo=UTC,
                    ).timestamp()
                ),
            )
            return Decimal(value=list(historicals.itertuples())[0].vwap)
        else:
            quotes: dict = self._provider.get_quote(tId=webull_id)
            if not quotes.get("askList"):
                return None
            rval = Decimal(quotes["askList"][0]["price"])
            return rval

    def _buy_instrument(
        self, symbol: str, qty: Optional[float], value: Optional[Money] = None
    ) -> dict:
        from webull import webull
        import requests

        # we should always have this at this point, as we would have had
        # to check price
        rtId: Optional[str] = self._local_instrument_cache.get(symbol)
        if not rtId:
            # webull uses '-' instead of '.'; BRK.B -> BRK-B
            lookup_symbol = symbol.replace(".", "-")
            tId = self._provider.get_ticker(lookup_symbol)
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
            quant=qty,
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
            orders_kwargs_list: List[Dict[str, float | Money | None]] = [
                {"qty": order, "value": None} for order in orders
            ]
        else:
            orders_kwargs_list = [{"qty": None, "value": value}]
        for order_kwargs in orders_kwargs_list:
            output = self._buy_instrument(ticker, **order_kwargs)  # type: ignore
            msg = output.get("msg")
            if not output.get("success"):
                if msg:
                    Logger.error(msg)
                    if "Your session has expired" in str(msg):
                        raise ConfigurationError(msg)
                    raise ValueError(msg)
                Logger.error(output)
                if "Your session has expired" in str(output):
                    raise ConfigurationError(output)
                raise ValueError(output)
        return True

    def get_unsettled_instruments(self) -> set[str]:
        orders = self._provider.get_current_orders()
        return set(item["ticker"]["symbol"] for item in orders)

    def _get_stock_info(self, ticker: str) -> dict:
        info = self._provider.get_ticker_info(ticker)
        return info

    def get_portfolio(self) -> dict:
        try:
            return self._provider.get_portfolio()
        except Exception as e:
            raise ConfigurationError(
                f"Could not fetch portfolio on {str(e)}; assuming session expired"
            )

    def get_positions(self) -> list[dict]:
        try:
            return self._provider.get_positions()
        except Exception as e:
            raise ConfigurationError(
                f"Could not fetch positions on {str(e)}; assuming session expired"
            )

    def get_holdings(self) -> RealPortfolio:
        accounts_data = self._get_cached_value(
            ObjectKey.ACCOUNT, callable=self.get_portfolio
        )
        my_stocks = self._get_cached_value(
            ObjectKey.POSITIONS, callable=self.get_positions
        )
        unsettled = self._get_cached_value(
            ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments
        )

        pre = {}
        symbols = []
        for row in my_stocks:
            local: Dict[str, Any] = {}
            local["units"] = row["position"]
            ticker = row["ticker"]["symbol"]
            local["ticker"] = ticker
            symbols.append(ticker)
            local["value"] = 0
            local["weight"] = 0
            pre[ticker] = local
        prices = self._price_cache.get_prices(symbols)
        total_value = Decimal(0.0)
        for s in symbols:
            price = prices[s]
            if not price:
                continue
            total_value += price * Decimal(pre[s]["units"])
        final = []
        pl_info = self.get_per_ticker_profit_or_loss()
        for s in symbols:
            local = pre[s]
            value = Decimal(prices[s] or 0) * Decimal(pre[s]["units"])
            local["value"] = Money(value=value)
            if value == 0.0000:
                local["weight"] = 0.0000
            else:
                local["weight"] = value / total_value
            local["unsettled"] = s in unsettled
            local["appreciation"] = pl_info[s].appreciation
            local["dividends"] = pl_info[s].dividends
            final.append(local)
        out = [RealPortfolioElement(**row) for row in final]
        cash = Decimal(accounts_data["cashBalance"])
        return RealPortfolio(holdings=out, cash=Money(value=cash), provider=self)

    def _get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        batches: List[Dict[str, Optional[Decimal]]] = []
        if at_day:
            batch_size = 1
        else:
            batch_size = 100
        for list_batch in divide_into_batches(tickers, batch_size):
            # TODO: determine if there is a bulk API
            wb_ids: Dict[str, str] = {}
            new_ids = False
            for ticker in list_batch:
                lookup_ticker = ticker.replace(".", "-")

                webull_id = self._local_instrument_cache.get(ticker)
                if not webull_id:
                    # skip the call
                    try:
                        webull_id = str(self._provider.get_ticker(lookup_ticker))
                    except Exception as e:
                        raise PriceFetchError([ticker], e)
                    self._local_instrument_cache[ticker] = webull_id
                    new_ids = True

                wb_ids[webull_id] = ticker
            if new_ids:
                self._save_local_instrument_cache()
            if at_day:
                output: dict[str, Decimal | None] = {}
                for _, ticker in wb_ids.items():
                    historicals = self._provider.get_bars(
                        stock=ticker,
                        interval="d1",
                        timeStamp=int(
                            datetime(
                                day=at_day.day,
                                month=at_day.month,
                                year=at_day.year,
                                tzinfo=UTC,
                            ).timestamp()
                        ),
                    )
                    base = list(historicals.itertuples())[0]
                    if base:
                        output[ticker] = Decimal(value=base[0].vwap)
                    else:
                        output[ticker] = None
                batches.append(output)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                final: Dict[str, Decimal | None] = {}
                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = {
                        executor.submit(self._provider.get_quote, None, wbid)
                        for wbid in wb_ids
                    }
                    for future in as_completed(futures):
                        future_output = future.result()
                        ticker = wb_ids[str(future_output["tickerId"])]
                        if "askList" in future_output:
                            value = future_output["askList"][0]["price"]
                            final[ticker] = Decimal(value=value)
                        else:
                            final[ticker] = None
                batches.append(final)
        prices: Dict[str, Optional[Decimal]] = {}
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
        my_stocks = self._get_cached_value(
            ObjectKey.POSITIONS, callable=self._provider.get_positions
        )
        dividends = self._get_dividends()
        base = {
            x["ticker"]["symbol"]: ProfitModel(
                appreciation=Money(value=Decimal(x["unrealizedProfitLoss"])),
                dividends=dividends[x["ticker"]["symbol"]],
            )
            for x in my_stocks
        }
        for k, v in dividends.items():
            if k not in base:
                base[k] = ProfitModel(appreciation=Money(value=0), dividends=v)
        return base

    def _get_dividends(self) -> defaultdict[str, Money]:
        dividends: dict = self._get_cached_value(
            ObjectKey.DIVIDENDS, callable=self._provider.get_dividends
        )
        dlist = dividends.get("dividendList", [])
        base = []
        for item in dlist:
            base.append(
                {
                    "value": Money(value=Decimal(item["dividendAmount"])),
                    "ticker": item["tickerTuple"]["symbol"],
                }
            )
        final: DefaultDict[str, Money] = defaultdict(lambda: Money(value=0))
        for item in base:
            final[item["ticker"]] += item["value"]
        return final

    def get_dividend_details(
        self, start: datetime | None = None
    ) -> list[DividendResult]:
        dividends: dict = self._get_cached_value(
            ObjectKey.DIVIDENDS, callable=self._provider.get_dividends
        )
        dlist = dividends.get("dividendList", [])
        final = []
        for x in dlist:
            paid_date = datetime.strptime(x["payDate"], r"%m/%d/%Y").date()
            if start and paid_date < start.date():
                continue
            final.append(
                DividendResult(
                    ticker=x["tickerTuple"]["symbol"],
                    amount=Money(value=float(x["dividendAmount"])),
                    date=paid_date,
                    provider=self.PROVIDER,
                )
            )
        return final


class WebullPaperProvider(WebullProvider):
    PROVIDER = ProviderType.WEBULL_PAPER
    PASSWORD_ENV = "WEBULL_PAPER_PASSWORD"
    USERNAME_ENV = "WEBULL_PAPER_USERNAME"
    TRADE_TOKEN_ENV = "WEBULL_PAPER_TRADE_TOKEN"
    DEVICE_ID_ENV = "WEBULL_PAPER_DEVICE_ID"

    def _get_provider(self):
        from webull import paper_webull

        return paper_webull
