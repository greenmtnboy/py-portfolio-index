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
from py_portfolio_index.portfolio_providers.common import PriceCache
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    CacheKey,
)
from py_portfolio_index.exceptions import ConfigurationError
from collections import defaultdict
from py_portfolio_index.exceptions import OrderError
from py_portfolio_index.enums import Provider
from functools import lru_cache
from os import environ, remove
from pytz import UTC
from pathlib import Path
import json
from platformdirs import user_cache_dir

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50

CACHE_PATH = "schwab_tickers.json"


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


def api_helper(response):
    from httpx import Response
    raw:Response = response
    raw.raise_for_status()
    return raw.json()


class SchwabProvider(BaseProvider):
    """Provider for interacting with stocks held in
    Schwab
    """

    PROVIDER = Provider.SCHWAB
    SUPPORTS_BATCH_HISTORY = 0
    API_KEY_ENV = "SCHWAB_API_KEY"
    APP_SECRET_ENV = "SCHWAB_APP_SECRET"
    SUPPORTS_FRACTIONAL_SHARES = False
    # TRADE_TOKEN_ENV = "SCHWAB_TRADE_TOKEN"
    # DEVICE_ID_ENV = "SCHWAB_DEVICE_ID"



    def __init__(
        self,
        api_key: str | None = None,
        app_secret: str | None = None,
        skip_cache: bool = False,
    ):
        if not api_key:
            api_key = environ.get(self.API_KEY_ENV, None)
        if not app_secret:
            app_secret = environ.get(self.APP_SECRET_ENV, None)
        # if not trade_token:
        #     trade_token = environ.get(self.TRADE_TOKEN_ENV, None)
        # if not device_id:
        #     device_id = environ.get(self.DEVICE_ID_ENV, None)
        if not (api_key and app_secret):
            raise ConfigurationError(
                "Must provide ALL OF api_key and app_secret arguments or set environment variables SCHWAB_API_KEY, SCHWAB_APP_SECRET "
            )

        token_path = Path(user_cache_dir( CACHE_DIR, ensure_exists=True)) / "schwab_token.json"
        from schwab import auth
        from schwab.utils import Utils
        try:
            c = auth.client_from_token_file(token_path, api_key, app_secret=app_secret)
        except FileNotFoundError:
            c = auth.client_from_login_flow(
                api_key, app_secret, 'https://127.0.0.1:8182', token_path,interactive=False)
        # we must set both of these to have a valid login
        BaseProvider.__init__(self)
        self._provider = c
        try:
            self._account_hash = api_helper(self._provider.get_account_numbers())[0]['hashValue']
        except Exception as e:
            remove(token_path)
            raise ConfigurationError(f"Authentication is expired: {str(e)}. Removed token.")
        self._utils = Utils(self._provider, account_hash = self._account_hash)
        self._price_cache: PriceCache = PriceCache(fetcher=self._get_instrument_prices)
        self._local_instrument_cache: Dict[str, str] = {}
        if not skip_cache:
            self._load_local_instrument_cache()

    def _load_local_instrument_cache(self):
        path = Path(user_cache_dir( CACHE_DIR, ensure_exists=True))
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

        path = Path(user_cache_dir( CACHE_DIR, ensure_exists=True))
        file = path / CACHE_PATH
        with open(file, "w") as f:
            json.dump(self._local_instrument_cache, f)

    @lru_cache(maxsize=None)
    def _get_instrument_price(
        self, ticker: str, at_day: Optional[date] = None
    ) -> Optional[Decimal]:
        stored = self._price_cache.get_prices(tickers=[ticker], date=at_day)
        if stored:
            return stored[ticker]
        if at_day:
            historicals = api_helper(self._provider.get_price_history_every_day(
                symbol=ticker,
                start_datetime = at_day,
                end_datetime = at_day,
            ))
            rval = Decimal(value=list(historicals[0].vwap))

        else:
            quotes = api_helper(self._provider.get_quote(symbol=ticker))
            print(quotes)
            rval = Decimal(value = quotes['quotes'])
        return rval


    def _buy_instrument(
        self, symbol: str, qty: Optional[float], value: Optional[Money] = None, price: Optional[Decimal] = None
    ) -> dict:
        from schwab.orders.equities import equity_buy_market, Duration, Session, equity_buy_limit
        from httpx import Request
        # if value:
        #     raise NotImplementedError("Schwab does not support buying by value")
        order:Request = self._provider.place_order(
            self._account_hash,
            order_spec = equity_buy_market(symbol, quantity=int(qty))
            .set_duration(Duration.DAY)
            .set_session(Session.SEAMLESS)
            .build()
        )
        id = self._utils.extract_order_id(order)
        return order

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        # if qty:
        #     float_qty = float(qty)
        #     import math

        #     if float_qty > 1:
        #         remainder_part, int_part = math.modf(float_qty)

        #         orders = [int(int_part), round(remainder_part, 4)]
        #     else:
        #         orders = [float_qty]
        #     orders_kwargs_list: List[Dict[str, float | Money | None]] = [
        #         {"qty": order, "value": None} for order in orders
        #     ]
        # else:
        # schwab we should always buy by value
        orders_kwargs_list = [{"qty": qty, "value": None, "price": self.get_instrument_price(ticker)}]
        for order_kwargs in orders_kwargs_list:
            try:
                output = self._buy_instrument(ticker, **order_kwargs)  # type: ignore
                print(output)
                # msg = output.get("msg")
                # if not output.get("success"):
                #     if msg:
                #         Logger.error(msg)
                #         if "Your session has expired" in str(msg):
                #             raise ConfigurationError(msg)
                #         raise ValueError(msg)
                #     Logger.error(output)
                #     if "Your session has expired" in str(output):
                #         raise ConfigurationError(output)
                #     raise ValueError(output)
            except Exception as e:
                raise OrderError(f"Could not buy {ticker}: {str(e)}")
        return True

    def get_unsettled_instruments(self) -> set[str]:
        orders = []
        for status in (self._provider.Order.Status.PENDING_ACTIVATION, self._provider.Order.Status.QUEUED,self._provider.Order.Status.WORKING):
            orders = self._provider.get_orders_for_account(status=status)
        return set(item["ticker"]["symbol"] for item in orders)

    def _get_stock_info(self, ticker: str) -> dict:
        info = self._provider.get_ticker_info(ticker)
        return info

    def get_portfolio(self) -> dict:
        try:
            return api_helper(self._provider.get_account(account_hash=self._account_hash))
        except KeyError as e:
            raise ConfigurationError(
                f"Could not fetch portfolio on {str(e)}; assuming session expired"
            )

    def get_holdings(self) -> RealPortfolio:
        accounts_data = self._get_cached_value(
            CacheKey.ACCOUNT, callable=self.get_portfolio
        )

        print(accounts_data)
        my_stocks = accounts_data.positions

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
        prices = self._price_cache.get_prices(symbols)
        total_value = Decimal(0.0)
        for s in symbols:
            if not prices[s]:
                continue
            total_value += prices[s] * Decimal(pre[s]["units"])
        final = []
        pl_info = self.get_per_ticker_profit_or_loss()
        for s in symbols:
            local = pre[s]
            value = Decimal(prices[s] or 0) * Decimal(pre[s]["units"])
            local["value"] = Money(value=value)
            local["weight"] = value / total_value
            local["unsettled"] = s in unsettled
            local["appreciation"] = pl_info[s].appreciation
            local["dividends"] = pl_info[s].dividends
            final.append(local)
        out = [RealPortfolioElement(**row) for row in final]
        cash = Decimal(accounts_data["cashBalance"])
        return RealPortfolio(holdings=out, cash=Money(value=cash), provider=self)

    def get_instrument_prices(self, tickers: List[str], at_day: Optional[date] = None):
        return self._price_cache.get_prices(tickers=tickers, date=at_day)

    def _get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        batches: List[Dict[str, Optional[Decimal]]] = []
        for list_batch in divide_into_batches(tickers, 100):
            if at_day:
                for ticker in list_batch:
                    historicals = api_helper(self._provider.get_price_history_every_day(
                        symbol=ticker,
                        start_datetime = at_day,
                        end_datetime = at_day,
                    ))
                    batches.append(
                        {ticker: Decimal(value=list(historicals[0].vwap))}
                    )
            else:
                quotes = api_helper(self._provider.get_quotes(symbols=list_batch))
                print(quotes)
                batches.append({ticker:Decimal(value=quotes[ticker]['quote']['lastPrice']) for ticker in list_batch})
        prices: Dict[str, Optional[Decimal]] = {}
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices
    
    def _get_account(self):
        from schwab.client import Client
        return api_helper(self._provider.get_account(Client.Account.Fields.POSITIONS))

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
        account_info = self._get_cached_value(
            CacheKey.ACCOUNT, callable=self._get_account
        )

        return {
            x["ticker"]["symbol"]: ProfitModel(
                appreciation=Money(value=Decimal(x["unrealizedProfitLoss"])),
                # dividends=dividends[x["ticker"]["symbol"]],
            )
            for x in account_info["positions"]
        }

    def _get_dividends(self) -> DefaultDict[str, Money]:
        dividends: dict = self._get_cached_value( CacheKey.DIVIDENDS, callable=self._provider.get_dividends)
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
