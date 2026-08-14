import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from os import environ, remove
from pathlib import Path
from time import sleep
from typing import Any

from platformdirs import user_cache_dir
from pytz import UTC

from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.constants import CACHE_DIR, UNKNOWN_TICKER, Logger
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.exceptions import ConfigurationError, OrderError
from py_portfolio_index.models import (
    DividendResult,
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

FRACTIONAL_SLEEP = 60
BATCH_SIZE = 50
FRACTIONAL_SLEEP = 60

CACHE_PATH = "schwab_tickers.json"
CACHE_DESC_PATH = "schwab_desc_to_ticker.json"

HARD_CODED_DESC_TO_TICKER: dict[str, str] = {
    "AT&T INC": "T",
    "ALBERTSONS CO SHS CLASS CLASS A": "ACI",
    "ATLANTICA SUSTAINABLE F": "AY",
    "ATLANTICA SUSTAINABLE FMANDATORY MERGER": "AY",
    "EATON CORP PLC F": "ETN",
    "S&P GLOBAL INC": "SPGI",
    "INVESCO LTD F": "IVZ",
    "SCHWAB1 INT 10/30-11/26": "SCHW",
    "AMERICAN FINL GROUP INC": "AFG",
    "FORTIVE CORP DISC TRADES WITH DUE BILLS": "FTV",
    "GOLDMAN SACHS GROUP INC": "GS",
    "SELECTIVE INS GROUP INC": "SIGI",
    "FIRST BANCORP P R F": "FBP",
    "NOBLE CORP PLC F": "NE",
    "MEDTRONIC PLC F": "MDT",
    "DELTA AIR LINES INC DEL": "DAL",
}


def date_to_datetimes(at_day: date) -> tuple[datetime, datetime]:
    start_datetime = datetime(
        day=at_day.day,
        year=at_day.year,
        month=at_day.month,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=UTC,
    )
    end_datetime = datetime(
        day=at_day.day,
        year=at_day.year,
        month=at_day.month,
        hour=23,
        minute=59,
        second=59,
        microsecond=0,
        tzinfo=UTC,
    )
    return start_datetime, end_datetime


# Substrings that indicate the Schwab/OAuth session is no longer valid and the
# user must re-authenticate. These can surface either from raise_for_status() on
# a bad response or, more commonly, from authlib raising an OAuthError while it
# tries to refresh an expired/revoked token *before* the request is even sent
# (e.g. "unsupported_token_type: 400 Bad Request: ...invalid_grant...").
AUTH_ERROR_SIGNATURES = (
    "exception while authenticating refresh token",
    "refresh token is invalid",
    "invalid_grant",
    "unsupported_token_type",
    "refresh_token",
)


def is_auth_error(e: Exception) -> bool:
    message = str(e).lower()
    return any(signature in message for signature in AUTH_ERROR_SIGNATURES)


def api_helper(request):
    """Execute a Schwab API call and return its JSON body.

    ``request`` must be a callable (thunk) so that the request -- including the
    implicit token refresh authlib performs before sending -- runs *inside* this
    try block. A failed refresh on an expired/revoked token is converted into a
    ConfigurationError so callers force a re-sign in rather than leaking a raw
    OAuthError.
    """
    from httpx import Response

    try:
        raw: Response = request()
        raw.raise_for_status()
    except ConfigurationError:
        raise
    except Exception as e:
        if is_auth_error(e):
            raise ConfigurationError(str(e))
        raise
    return raw.json()


def safe_get_symbol(input: dict[str, Any]) -> str:
    return input.get("instrument", {}).get("symbol", UNKNOWN_TICKER)


class SchwabProvider(BaseProvider):
    """Provider for interacting with stocks held in
    Schwab
    """

    PROVIDER = ProviderType.SCHWAB
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
        external_auth: bool = False,
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
            raise ConfigurationError("Must provide ALL OF api_key and app_secret arguments or set environment variables SCHWAB_API_KEY, SCHWAB_APP_SECRET ")

        token_path = Path(user_cache_dir(CACHE_DIR, ensure_exists=True)) / "schwab_token.json"
        from schwab import auth
        from schwab.utils import Utils

        try:
            c = auth.client_from_token_file(token_path, api_key, app_secret=app_secret)
        except FileNotFoundError:
            if external_auth:
                raise ConfigurationError("External authentication flag set but invalid auth")

            c = auth.client_from_login_flow(
                api_key,
                app_secret,
                "https://127.0.0.1:8182",
                token_path,
                interactive=external_auth,
            )
        # we must set both of these to have a valid login
        BaseProvider.__init__(self)
        self._provider = c
        try:
            self._account_hash = api_helper(self._provider.get_account_numbers)[0]["hashValue"]
        except Exception as e:
            remove(token_path)
            raise ConfigurationError(f"Authentication is expired: {e!s}. Removed token.")
        self._utils = Utils(self._provider, account_hash=self._account_hash)
        self._local_description_lookup_cache: dict[str, str] = {}
        if not skip_cache:
            self._load_local_description_lookup_cache()

    def _load_local_description_lookup_cache(self):
        import json
        from pathlib import Path

        from platformdirs import user_cache_dir

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
        file = path / CACHE_DESC_PATH
        if not file.exists():
            self._local_description_lookup_cache = {}
            return
        with open(file, "r") as f:
            self._local_description_lookup_cache = json.load(f)

    def _save_local_description_lookup_cache(self):
        import json
        from pathlib import Path

        from platformdirs import user_cache_dir

        path = Path(user_cache_dir("py_portfolio_index", ensure_exists=True))
        file = path / CACHE_DESC_PATH
        with open(file, "w") as f:
            json.dump(self._local_description_lookup_cache, f)

    @instance_cache
    def _get_instrument_price(self, ticker: str, at_day: date | None = None, fail_on_missing: bool = True) -> Decimal | None:
        stored = self._price_cache.get_prices(tickers=[ticker], date=at_day)
        if stored:
            return stored[ticker]
        if at_day:
            start_datetime, end_datetime = date_to_datetimes(at_day)
            historicals = api_helper(
                lambda: self._provider.get_price_history_every_day(
                    symbol=ticker,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                )
            )
            rval = Decimal(value=historicals[0].vwap)

        else:
            quotes = api_helper(lambda: self._provider.get_quote(symbol=ticker))
            rval = Decimal(value=quotes["quotes"])
        return rval

    def _buy_instrument(
        self,
        symbol: str,
        qty: int,
        value: Money | None = None,
        price: Decimal | None = None,
    ) -> None:
        from httpx import Request
        from schwab.orders.equities import Duration, Session, equity_buy_market

        order: Request = self._provider.place_order(
            self._account_hash,
            order_spec=equity_buy_market(symbol, quantity=int(qty)).set_duration(Duration.DAY).set_session(Session.NORMAL).build(),
        )
        try:
            _ = self._utils.extract_order_id(order)
        except Exception as e:
            if "order not successful: status 429" in str(e):
                Logger.info(f"RH error: was throttled on fractional orders! Sleeping {FRACTIONAL_SLEEP}")
                sleep(FRACTIONAL_SLEEP)
                return self._buy_instrument(symbol=symbol, qty=qty, value=value, price=price)
            raise
        return None

    def buy_instrument(self, ticker: str, qty: Decimal, value: Money | None = None):
        if qty:
            orders_kwargs: dict[str, Decimal | Money | None] = {
                "qty": qty,
                "value": None,
            }
        else:
            orders_kwargs = {"qty": None, "value": value}

        try:
            self._buy_instrument(ticker, **orders_kwargs)  # type: ignore
        except Exception as e:
            if is_auth_error(e):
                raise ConfigurationError(f"Could not buy {ticker}: {e!s}; assuming session expired")
            raise OrderError(f"Could not buy {ticker}: {e!s}")
        return True

    def get_unsettled_instruments(self) -> set[str]:
        orders = []
        for status in (
            self._provider.Order.Status.PENDING_ACTIVATION,
            self._provider.Order.Status.QUEUED,
            self._provider.Order.Status.WORKING,
        ):
            orders = api_helper(lambda status=status: self._provider.get_orders_for_account(account_hash=self._account_hash, status=status))
        return {safe_get_symbol(item) for item in orders}

    def _get_stock_info(self, ticker: str) -> dict:
        return api_helper(
            lambda: self._provider.get_instruments(
                symbols=[ticker],
                project=self._provider.Instrument.Projection.FUNDAMENTAL,
            )
        )

    def _get_stock_info_fuzzy(self, search: str) -> dict:
        # do our best to match a description to a ticker
        STRIP_VALUES = ["FCLASS", "CLASS", "CLASS EQUITY"]
        for strip in STRIP_VALUES:
            search = search.replace(strip, "").strip()
        search = search.strip()
        search = re.sub(r"\s+", r" ", search)
        search = f"(?i){search[:20]}.*"
        search = search.replace("&", ".")
        return api_helper(
            lambda: self._provider.get_instruments(
                symbols=[search],
                projection=self._provider.Instrument.Projection.DESCRIPTION_REGEX,
            )
        )

    def get_portfolio(self) -> dict:
        from schwab.client import Client

        try:
            return api_helper(
                lambda: self._provider.get_account(
                    account_hash=self._account_hash,
                    fields=Client.Account.Fields.POSITIONS,
                )
            )["securitiesAccount"]
        except ConfigurationError:
            raise
        except KeyError as e:
            raise ConfigurationError(f"Could not fetch portfolio on {e!s}; assuming session expired")
        except Exception as e:
            if is_auth_error(e):
                raise ConfigurationError(f"Could not fetch portfolio: {e!s}; assuming session expired")
            raise

    def get_holdings(self) -> RealPortfolio:
        accounts_data = self._get_cached_value(ObjectKey.ACCOUNT, callable=self.get_portfolio)
        my_stocks = accounts_data["positions"]

        unsettled = self._get_cached_value(ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments)

        pre = {}
        symbols = []
        for row in my_stocks:
            local: dict[str, Any] = {}
            local["units"] = row["longQuantity"]
            # unclear what is happening here, but skip this for now
            ticker = safe_get_symbol(row)
            local["ticker"] = ticker
            symbols.append(ticker)
            local["value"] = Money(value=row["marketValue"])
            local["weight"] = 0
            pre[ticker] = local
        prices = self._price_cache.get_prices(symbols)
        total_value = Decimal("0.0")
        for s in symbols:
            price = prices[s]
            if not price:
                continue
            total_value += price * Decimal(pre[s]["units"])
        final = []
        pl_info = self.get_per_ticker_profit_or_loss()
        for s in symbols:
            local = pre[s]
            local["weight"] = local["value"].value / total_value
            local["unsettled"] = s in unsettled
            local["appreciation"] = pl_info[s].appreciation
            local["dividends"] = pl_info[s].dividends
            final.append(local)
        out = [RealPortfolioElement(**row) for row in final]
        cash = Decimal(accounts_data["currentBalances"]["cashBalance"])
        return RealPortfolio(holdings=out, cash=Money(value=cash), provider=self)

    def _get_instrument_prices(
        self,
        tickers: list[str],
        at_day: date | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal | None]:
        batches: list[dict[str, Decimal | None]] = []
        prices: dict[str, Decimal | None] = {}

        for list_batch in divide_into_batches(tickers, 100):
            if at_day:
                start_datetime, end_datetime = date_to_datetimes(at_day)
                for ticker in list_batch:
                    historicals = api_helper(
                        lambda ticker=ticker, start_datetime=start_datetime, end_datetime=end_datetime: self._provider.get_price_history_every_day(
                            symbol=ticker,
                            start_datetime=start_datetime,
                            end_datetime=end_datetime,
                        )
                    )
                    batches.append({ticker: Decimal(value=historicals["candles"][0]["close"])})
            else:
                quotes = api_helper(lambda list_batch=list_batch: self._provider.get_quotes(symbols=list_batch))
                for ticker in list_batch:
                    if ticker in quotes:
                        prices[ticker] = Decimal(value=quotes[ticker]["quote"]["lastPrice"])
                    else:
                        prices[ticker] = None
        for fbatch in batches:
            prices = {**prices, **fbatch}
        return prices

    def _get_dividends_wrapper(self):
        from schwab.client.base import BaseClient

        return api_helper(
            lambda: self._provider.get_transactions(
                account_hash=self._account_hash,
                transaction_types=BaseClient.Transactions.TransactionType.DIVIDEND_OR_INTEREST,
            )
        )

    def get_per_ticker_profit_or_loss(self) -> dict[str, ProfitModel]:
        account_info = self._get_cached_value(ObjectKey.ACCOUNT, callable=self.get_portfolio)

        dividends = self._get_dividends()

        first = {
            safe_get_symbol(x): ProfitModel(
                appreciation=Money(value=Decimal(x["longOpenProfitLoss"])),
                dividends=dividends[safe_get_symbol(x)],
            )
            for x in account_info["positions"]
        }
        for k, v in dividends.items():
            if k not in first:
                first[k] = ProfitModel(appreciation=Money(value=0), dividends=v)
        return first

    def _fuzzy_ticker_lookup(self, lookup_desc: str) -> tuple[str, bool]:
        changes = False
        ticker = self._local_description_lookup_cache.get(lookup_desc)
        if not ticker:
            ticker = HARD_CODED_DESC_TO_TICKER.get(
                lookup_desc,
            )
        if not ticker:
            fuzzy_search = self._get_stock_info_fuzzy(search=lookup_desc)
            if fuzzy_search.get("instruments"):
                match = fuzzy_search["instruments"][0]
                self._local_description_lookup_cache[lookup_desc] = match["symbol"]
                changes = True
            else:
                ticker = lookup_desc
        return ticker or UNKNOWN_TICKER, changes

    def _get_dividends(self) -> defaultdict[str, Money]:
        dividends: dict = self._get_cached_value(ObjectKey.DIVIDENDS_DETAIL, callable=self._get_dividends_wrapper)
        base: list[dict] = []

        changes: bool = False
        for item in dividends:
            ticker, item_searched = self._fuzzy_ticker_lookup(item["description"])
            changes = changes or item_searched
            base.append({"value": Money(value=Decimal(item["netAmount"])), "ticker": ticker})
        if changes:
            self._save_local_description_lookup_cache()
        final: defaultdict[str, Money] = defaultdict(lambda: Money(value=0))
        for dividend_item in base:
            final[dividend_item["ticker"]] += dividend_item["value"]
        return final

    def get_dividend_details(self, start: datetime | None = None) -> list[DividendResult]:
        dividends: dict = self._get_cached_value(ObjectKey.DIVIDENDS, callable=self._get_dividends_wrapper)
        final = []
        changes = False
        for item in dividends:
            ticker, item_searched = self._fuzzy_ticker_lookup(item["description"])
            changes = changes or item_searched
            if item["status"] == "VALID":
                event_date = datetime.fromisoformat(item["time"]).date()
                if start and event_date < start.date():
                    continue

                final.append(
                    DividendResult(
                        ticker=ticker,
                        amount=Money(value=float(item["netAmount"])),
                        date=event_date,
                        provider=self.PROVIDER,
                        external_id=str(item["activityId"]),
                    )
                )
        if changes:
            self._save_local_description_lookup_cache()
        return final
