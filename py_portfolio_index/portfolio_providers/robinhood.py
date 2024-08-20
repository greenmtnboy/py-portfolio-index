import re
from decimal import Decimal
from time import sleep
from datetime import date, datetime
from typing import Optional, List, Dict, DefaultDict
from py_portfolio_index.constants import Logger
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
from py_portfolio_index.exceptions import PriceFetchError, ConfigurationError
from py_portfolio_index.portfolio_providers.helpers.robinhood import (
    validate_login,
    ROBINHOOD_PASSWORD_ENV,
    ROBINHOOD_USERNAME_ENV,
)
from py_portfolio_index.enums import Provider
from os import environ
from collections import defaultdict

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50


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


class RobinhoodProvider(BaseProvider):
    """Provider for interacting with stocks held in
    Robinhood.
    """

    PROVIDER = Provider.ROBINHOOD
    SUPPORTS_BATCH_HISTORY = 70

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        skip_cache: bool = False,
        external_auth: bool = False,
    ):
        import robin_stocks.robinhood as r

        if not username:
            username = environ.get(ROBINHOOD_USERNAME_ENV, None)
        if not password:
            password = environ.get(ROBINHOOD_PASSWORD_ENV, None)
        if not (username and password):
            raise ConfigurationError(
                "Must provide username and password arguments or set environment variables ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD "
            )
        self._provider = r
        BaseProvider.__init__(self)
        if not external_auth:
            self._provider.login(username=username, password=password)
        else:
            validate_login()
        self._local_instrument_cache: List[Dict] = []
        if not skip_cache:
            self._load_local_instrument_cache()

    @property
    def valid_assets(self) -> set[str]:
        if not self._local_instrument_cache:
            self._load_local_instrument_cache()
        return self._get_cached_value(
            CacheKey.MISC,
            value="valid_tickers",
            callable=lambda: {row["symbol"] for row in self._local_instrument_cache},
        )

    def _load_local_instrument_cache(self):
        from platformdirs import user_cache_dir
        from pathlib import Path
        import json

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
        file = path / "robinhood_instruments.json"
        if not file.exists():
            self._local_instrument_cache = {}
            return
        with open(file, "r") as f:
            self._local_instrument_cache = json.load(f)

    def _save_local_instrument_cache(self):
        from platformdirs import user_cache_dir
        from pathlib import Path
        import json

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
        file = path / "robinhood_instruments.json"
        with open(file, "w") as f:
            json.dump(self._local_instrument_cache, f)

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
                [ticker],
                f"No historical data found for ticker {ticker} on date {at_day.isoformat()}",
            )
        else:
            quotes = self._provider.get_quotes([ticker])
            if not quotes[0]:
                return None
            rval = Decimal(quotes[0]["ask_price"])
            return rval

    def _buy_instrument(
        self, symbol: str, qty: float, value: Optional[Money] = None
    ) -> dict:
        """Custom function to enable evolution with the robinhood API"""
        from robin_stocks.robinhood.stocks import (
            orders_url,
            # request_post,
            SESSION,
            update_session,
        )
        from robin_stocks.robinhood.orders import (
            load_account_profile,
            get_latest_price,
            round_price,
        )
        from uuid import uuid4

        def request_post(
            url, payload=None, timeout=16, json=False, jsonify_data=True, retry: int = 1
        ):
            """For a given url and payload, makes a post request and returns the response. Allows for responses other than 200.

            :param url: The url to send a post request to.
            :type url: str
            :param payload: Dictionary of parameters to pass to the url as url/?key1=value1&key2=value2.
            :type payload: Optional[dict]
            :param timeout: The time for the post to wait for a response. Should be slightly greater than multiples of 3.
            :type timeout: Optional[int]
            :param json: This will set the 'content-type' parameter of the session header to 'application/json'
            :type json: bool
            :param jsonify_data: If this is true, will return requests.post().json(), otherwise will return response from requests.post().
            :type jsonify_data: bool
            :returns: Returns the data from the post request.

            """
            data = None
            res = None
            if json:
                update_session("Content-Type", "application/json")
                res = SESSION.post(url, json=payload, timeout=timeout)
                update_session(
                    "Content-Type", "application/x-www-form-urlencoded; charset=utf-8"
                )
            else:
                res = SESSION.post(url, data=payload, timeout=timeout)

            if res.status_code == 429:
                sleep(5 * retry)
                return request_post(
                    url, payload, timeout, json, jsonify_data, retry + 1
                )
            if res.status_code not in [
                200,
                201,
                202,
                204,
                301,
                302,
                303,
                304,
                307,
                400,
                401,
                402,
                403,
            ]:
                res.raise_for_status()

            data = res.json()
            if jsonify_data:
                return data
            else:
                return res

        price = round_price(
            next(iter(get_latest_price(symbol, "ask_price", False)), 0.00)
        )
        if value:
            qty = round(float(value.decimal) / price, 2)

        account = self._get_cached_value(
            CacheKey.MISC,
            value="account_id",
            callable=lambda: load_account_profile(account_number=None, info="url"),
        )
        sym_to_i = self._get_cached_value(
            CacheKey.MISC,
            value="symbol_to_instrument",
            callable=lambda: {
                row["symbol"]: row["url"] for row in self._local_instrument_cache
            },
        )
        payload = {
            "account": account,
            "instrument": sym_to_i[symbol],
            "order_form_version": "2",
            "preset_percent_limit": "0.05",
            "symbol": symbol,
            "price": price,
            "quantity": qty,
            "ref_id": str(uuid4()),
            "type": "limit",
            "time_in_force": "gfd",
            "trigger": "immediate",
            "side": "buy",
            "extended_hours": False,
        }

        url = orders_url()
        data = request_post(url, payload, json=True, jsonify_data=True)

        return data or {}

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None):
        float_qty = float(qty)
        output = self._buy_instrument(ticker, float_qty, value)
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
            return self.buy_instrument(ticker=ticker, qty=qty)
        elif msg and "Too many requests for fractional orders" in msg:
            Logger.info(
                f"RH error: was throttled on fractional orders! Sleeping {FRACTIONAL_SLEEP}"
            )
            sleep(FRACTIONAL_SLEEP)
            return self.buy_instrument(ticker=ticker, qty=qty)
        if not output.get("id"):
            if msg:
                Logger.error(msg)
                raise ValueError(msg)
            Logger.error(output)
            raise ValueError(output)
        return True

    def get_unsettled_instruments(self) -> set[str]:
        from robin_stocks.robinhood.orders import orders_url, request_get

        """We need to efficiently bypass
        paginating all orders if possible
        so just check the account info for if there
        is any cash held for orders first"""
        accounts_data = self._get_cached_value(
            CacheKey.ACCOUNT, callable=self._provider.load_account_profile
        )
        if not accounts_data:
            raise ConfigurationError("Could not load account profile, check login")
        if float(accounts_data.get("cash_held_for_orders", 0)) == 0:
            return set()

        url = orders_url()
        from datetime import datetime, timedelta

        window = datetime.now() - timedelta(days=7)
        data = request_get(url, "results", payload={"updated_at": window.isoformat()})
        orders = [item for item in data if item["cancel"] is not None]
        if len(orders) == len(data):
            # bit the bullet
            data = request_get(
                url, "paginate", payload={"updated_at": window.isoformat()}
            )
        instrument_to_symbol_map = {
            row["url"]: row["symbol"] for row in self._local_instrument_cache
        }
        for item in orders:
            item["symbol"] = instrument_to_symbol_map[item["instrument"]]
        return set(item["symbol"] for item in orders)

    def _refresh_local_instruments(self):
        from robin_stocks.robinhood.stocks import instruments_url, request_get

        instrument_url = instruments_url()
        instrument_info = request_get(instrument_url, dataType="pagination")
        self._local_instrument_cache = instrument_info
        self._save_local_instrument_cache()
        return self._local_instrument_cache

    def _process_cache_to_dict(self):
        return InstrumentDict(
            lambda: {
                row["url"]: row["symbol"] for row in self._refresh_local_instruments()
            },
            {row["url"]: row["symbol"] for row in self._local_instrument_cache},
        )

    def _get_local_instrument_symbol(
        self, instrument: str, refreshed: bool = False
    ) -> str:
        instrument_to_symbol_map = self._get_cached_value(
            CacheKey.MISC,
            value="instrument_to_symbol_map",
            callable=self._process_cache_to_dict,
        )
        try:
            out = instrument_to_symbol_map[instrument]
            return out
        except KeyError as e:
            if not refreshed:
                instrument_to_symbol_map = self._get_cached_value(
                    CacheKey.MISC,
                    value="instrument_to_symbol_map",
                    callable=self._process_cache_to_dict,
                    # force refresh
                    max_age_seconds=1,
                )
                return self._get_local_instrument_symbol(instrument, True)
            raise e

    def _get_stock_info(self, ticker: str) -> dict:
        matches = self._provider.find_instrument_data(ticker)
        for match in matches:
            if match["symbol"] == ticker:
                return {
                    "name": match["simple_name"],
                    "exchange": match["exchange"],
                    "market": match["market"],
                    "country": match["country"],
                    "tradable": bool(match["tradable"]),
                }
        return {}

    def get_holdings(self):
        accounts_data = self._get_cached_value(
            CacheKey.ACCOUNT, callable=self._provider.load_account_profile
        )
        my_stocks = self._get_cached_value(
            CacheKey.POSITIONS, callable=self._provider.get_open_stock_positions
        )
        unsettled = self._get_cached_value(
            CacheKey.UNSETTLED, callable=self.get_unsettled_instruments
        )

        pre = {}
        symbols = []
        instrument_to_symbol_map = self._get_cached_value(
            CacheKey.MISC,
            value="instrument_to_symbol_map",
            callable=self._process_cache_to_dict,
        )
        for row in my_stocks:
            local = {}
            local["units"] = row["quantity"]
            # instrument_data = self._provider.get_instrument_by_url(row["instrument"])
            ticker = instrument_to_symbol_map[row["instrument"]]
            local["ticker"] = ticker
            symbols.append(ticker)
            local["value"] = 0
            local["weight"] = 0
            pre[ticker] = local
        # grab this _after_, in case we had to refresh instruments
        inactive_stocks = {
            row["symbol"]
            for row in self._local_instrument_cache
            if row["state"] == "inactive"
        }
        symbols = [s for s in symbols if s not in inactive_stocks]
        prices = self.get_instrument_prices(symbols)
        total_value = Decimal(0.0)
        for s in symbols:
            price = prices[s]
            if not prices[s]:
                continue
            total_value += price * Decimal(pre[s]["units"])
        final = []
        pl = self.get_per_ticker_profit_or_loss()
        for s in symbols:
            local = pre[s]
            value = Decimal(prices[s] or 0) * Decimal(pre[s]["units"])
            local["value"] = Money(value=value)
            local["weight"] = value / total_value
            local["unsettled"] = s in unsettled
            if s in pl:
                local["appreciation"] = pl[s].appreciation
                local["dividends"] = pl[s].dividends
            final.append(local)
        out = [RealPortfolioElement(**row) for row in final]

        cash = Decimal(accounts_data["portfolio_cash"]) - Decimal(
            accounts_data["cash_held_for_orders"]
        )
        return RealPortfolio(holdings=out, cash=Money(value=cash), provider=self)

    def get_instrument_prices(self, tickers: List[str], at_day: Optional[date] = None):
        return self._price_cache.get_prices(tickers=tickers, date=at_day)

    def _get_instrument_prices(
        self, tickers: List[str], at_day: Optional[date] = None
    ) -> Dict[str, Optional[Decimal]]:
        ticker_list = tickers
        batches = []
        for batch in divide_into_batches(ticker_list, BATCH_SIZE):
            if at_day:
                historicals = self._provider.get_stock_historicals(
                    batch, interval="day", span="year", bounds="regular"
                )
                batches.append(
                    {s: nearest_multi_value(s, historicals, at_day) for s in batch}
                )
            else:
                results = self._provider.get_quotes(batch)
                batches.append({s: nearest_multi_value(s, results) for s in batch})
        prices: Dict[str, Optional[Decimal]] = {}
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
        my_stocks = self._get_cached_value(
            CacheKey.POSITIONS, callable=self._provider.get_open_stock_positions
        )
        instrument_to_symbol_map = self._get_cached_value(
            CacheKey.MISC,
            callable=self._process_cache_to_dict,
        )
        divs = self._get_cached_value(CacheKey.DIVIDENDS, callable=self._get_dividends)
        output = {}
        for x in my_stocks:
            historical_value = Decimal(x["average_buy_price"]) * Decimal(x["quantity"])
            ticker = instrument_to_symbol_map[x["instrument"]]
            try:
                current_price = self.get_instrument_price(ticker) or Decimal(0.0)
            except PriceFetchError:
                current_price = Decimal(0.0)
            current_value = current_price * Decimal(x["quantity"])
            pl = Money(value=current_value - historical_value)
            output[ticker] = ProfitModel(
                appreciation=pl, dividends=divs.get(ticker, Money(value=Decimal(0)))
            )
        return output

    def _get_dividends(self) -> DefaultDict[str, Money]:
        value = self._provider.get_dividends()
        output: DefaultDict[str, Money] = defaultdict(lambda: Money(value=0))
        instrument_to_symbol_map = self._get_cached_value(
            CacheKey.MISC,
            callable=self._process_cache_to_dict,
        )
        [
            item.update({"symbol": instrument_to_symbol_map[item["instrument"]]})
            for item in value
        ]
        for item in value:
            if item["state"] == "paid":
                output[item["symbol"]] += Money(value=float(item["amount"]))
        return output
