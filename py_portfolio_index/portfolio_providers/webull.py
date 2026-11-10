import json
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from functools import partial
from os import environ
from typing import Any

from py_portfolio_index.constants import CACHE_DIR, Logger
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.exceptions import (
    ConfigurationError,
    OrderError,
    PriceFetchError,
)
from py_portfolio_index.models import (
    DividendResult,
    Money,
    ProfitModel,
    RealPortfolio,
    RealPortfolioElement,
    Transaction,
)
from py_portfolio_index.portfolio_providers.base_portfolio import (
    BaseProvider,
    ObjectKey,
)
from py_portfolio_index.portfolio_providers.helpers.webull import (
    call_with_retries,
    get_api,
    get_server_exception,
)

CACHE_PATH = "webull_instruments.json"

DEFAULT_REGION_ID = "us"
DEFAULT_WEBULL_TIMEOUT = 60
DEFAULT_CURRENCY = "USD"

# /account/positions and the order listings require page_size in [10, 100]
MAX_PAGE_SIZE = 100
# guard against paginating forever if the API keeps reporting another page
MAX_PAGES = 100
# /market-data/history-bar caps the bar count per request
MAX_HISTORY_BARS = 1200

# Webull files instruments under a category, and rejects a symbol looked up
# under the wrong one. US equities land in one of these two, OTC names included.
US_EQUITY_CATEGORIES = ("US_STOCK", "US_ETF")

# Webull spells class shares with a space where this library uses a dot,
# e.g. BRK.B is "BRK B".
_SYMBOL_SEPARATOR = " "


def _to_webull_symbol(ticker: str) -> str:
    return ticker.replace(".", _SYMBOL_SEPARATOR)


def _from_webull_symbol(symbol: str) -> str:
    return symbol.replace(_SYMBOL_SEPARATOR, ".")


# exchange codes for over-the-counter instruments, which take limit orders only
OTC_EXCHANGES = ("OTC", "PIN", "GREY")
# how far past the quote an OTC limit order may fill
OTC_LIMIT_BUFFER = Decimal("0.01")


class WebullProvider(BaseProvider):
    """Provider for interacting with stocks held in Webull.

    Uses the official Webull OpenAPI SDK. Generate an app key and secret from
    the Webull developer portal for your region and either pass them in or set
    WEBULL_API_KEY and WEBULL_API_SECRET.

    Creating a provider signs in, which blocks until the sign-in is approved in
    the Webull app if no valid token is saved.
    """

    PROVIDER = ProviderType.WEBULL
    SUPPORTS_BATCH_HISTORY = 0
    SUPPORTS_LIMIT_ORDERS = True
    # the order path handles OTC (whole-share limit orders), but Webull rejects
    # OpenAPI OTC orders with OPENAPI_OTC_ACCOUNT_NOT_TRADE even once its
    # third-party OTC agreement is signed; turn this on once that clears
    SUPPORTS_OTC = False

    API_KEY_ENV = "WEBULL_API_KEY"
    API_SECRET_ENV = "WEBULL_API_SECRET"
    ACCOUNT_ID_ENV = "WEBULL_ACCOUNT_ID"
    REGION_ID_ENV = "WEBULL_REGION_ID"

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        account_id: str | None = None,
        region_id: str | None = None,
        timeout: int = DEFAULT_WEBULL_TIMEOUT,
        skip_cache: bool = False,
    ):
        if not app_key:
            app_key = environ.get(self.API_KEY_ENV, None)
        if not app_secret:
            app_secret = environ.get(self.API_SECRET_ENV, None)
        if not account_id:
            account_id = environ.get(self.ACCOUNT_ID_ENV, None)
        if not region_id:
            region_id = environ.get(self.REGION_ID_ENV, DEFAULT_REGION_ID)
        if not (app_key and app_secret):
            raise ConfigurationError("Must provide app_key and app_secret arguments or set environment " f"variables {self.API_KEY_ENV} and {self.API_SECRET_ENV}")

        self._api = get_api(app_key, app_secret, region_id, timeout=timeout)
        self._server_exception = get_server_exception()
        self._account_id = self._resolve_account_id(account_id)

        # symbol -> {"instrument_id": str, "category": str, "exchange": str}
        self._local_instrument_cache: dict[str, dict[str, str]] = {}
        if not skip_cache:
            self._load_local_instrument_cache()

        BaseProvider.__init__(self)

    ## setup

    def _resolve_account_id(self, account_id: str | None) -> str:
        try:
            response = call_with_retries(lambda: self._api.account.get_app_subscriptions())
        except self._server_exception as e:
            raise ConfigurationError(f"Could not list Webull accounts for these credentials: {e}")
        subscriptions = response.json() or []
        available = [row["account_id"] for row in subscriptions if row.get("account_id")]
        if not available:
            raise ConfigurationError("These Webull credentials are not subscribed to any account.")
        if account_id:
            if account_id not in available:
                raise ConfigurationError(f"Account {account_id} is not available to these credentials; " f"found {available}")
            return account_id
        if len(available) > 1:
            Logger.warning(f"Webull credentials cover multiple accounts {available}; defaulting " f"to {available[0]}. Pass account_id or set {self.ACCOUNT_ID_ENV} to " "choose a different one.")
        return available[0]

    def _cache_file(self):
        from pathlib import Path

        from platformdirs import user_cache_dir

        return Path(user_cache_dir(CACHE_DIR, ensure_exists=True)) / CACHE_PATH

    def _load_local_instrument_cache(self):
        file = self._cache_file()
        if not file.exists():
            self._local_instrument_cache = {}
            return
        try:
            with open(file, "r") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError):
            cached = None
        # corruption guard, and a guard against older cache formats, which held
        # a bare instrument id per symbol, then no exchange
        if not isinstance(cached, dict) or not all(isinstance(v, dict) and "exchange" in v for v in cached.values()):
            cached = {}
        self._local_instrument_cache = cached

    def _save_local_instrument_cache(self):
        with open(self._cache_file(), "w") as f:
            json.dump(self._local_instrument_cache, f)

    ## instrument resolution

    def _lookup_instrument(self, ticker: str) -> dict | None:
        symbol = _to_webull_symbol(ticker)
        for category in US_EQUITY_CATEGORIES:
            try:
                response = call_with_retries(partial(self._api.instrument.get_instrument, symbol, category))
            except self._server_exception as e:
                if getattr(e, "get_error_code", lambda: None)() != "INVALID_SYMBOL":
                    raise
                continue
            for row in response.json() or []:
                if row.get("symbol") == symbol:
                    return {**row, "category": row.get("category") or category}
        return None

    def _get_instrument(self, ticker: str) -> dict[str, str]:
        """Resolve a ticker to its Webull instrument id, category and exchange."""
        cached = self._local_instrument_cache.get(ticker)
        if cached:
            return cached
        row = self._lookup_instrument(ticker)
        if not row:
            raise OrderError(f"Could not find a Webull instrument for {ticker}")
        instrument = {
            "instrument_id": str(row["instrument_id"]),
            "category": row["category"],
            "exchange": row.get("exchange_code") or "",
        }
        self._local_instrument_cache[ticker] = instrument
        self._save_local_instrument_cache()
        return instrument

    ## pricing

    def _preview_price(self, ticker: str) -> Decimal | None:
        """The share price Webull would charge, from a preview of a one share buy.

        Market data needs a paid subscription and doesn't cover OTC names, while
        a preview needs neither and places nothing.
        """
        order = self._order(ticker, Decimal(1), "BUY", {"order_type": "MARKET"})
        try:
            response = call_with_retries(partial(self._api.order_v3.preview_order, self._account_id, [order]))
        except self._server_exception as e:
            Logger.error(f"Could not get a Webull price for {ticker}: {e}")
            return None
        cost = (response.json() or {}).get("estimated_cost")
        return Decimal(cost) if cost else None

    def _get_instrument_prices(
        self,
        tickers: list[str],
        at_day: date | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal | None]:
        if at_day:
            prices: dict[str, Decimal | None] = {}
            for ticker in tickers:
                prices[ticker] = self._get_historical_price(ticker, at_day, fail_on_missing=fail_on_missing)
            return prices

        prices = {ticker: self._preview_price(ticker) for ticker in tickers}
        missing = [ticker for ticker, price in prices.items() if price is None]
        if missing and fail_on_missing:
            found = {k: v for k, v in prices.items() if v is not None}
            raise PriceFetchError(missing, f"No Webull price found for {missing}", prices=found)
        return prices

    def _get_instrument_price(self, ticker: str, at_day: date | None = None, fail_on_missing: bool = True) -> Decimal | None:
        return self._get_instrument_prices([ticker], at_day=at_day, fail_on_missing=fail_on_missing)[ticker]

    def _get_historical_price(self, ticker: str, at_day: date, fail_on_missing: bool = True) -> Decimal | None:
        """Close on the last session at or before at_day.

        Webull's bar API only takes a count of bars back from today, so we ask
        for enough to span the gap and then scan for the target day.
        """
        try:
            instrument = self._get_instrument(ticker)
        except OrderError as e:
            if fail_on_missing:
                raise PriceFetchError([ticker], e)
            return None
        days_back = (datetime.now(tz=timezone.utc).date() - at_day).days
        if days_back < 0:
            raise PriceFetchError([ticker], f"{at_day} is in the future")
        # pad for weekends and holidays, which don't produce bars
        count = min(days_back + 10, MAX_HISTORY_BARS)
        try:
            response = call_with_retries(
                lambda: self._api.market_data.get_history_bar(
                    _to_webull_symbol(ticker),
                    instrument["category"],
                    "D",
                    count=str(count),
                )
            )
        except self._server_exception as e:
            if fail_on_missing:
                raise PriceFetchError([ticker], e)
            return None
        # bars arrive newest first; the first at or before the target day wins
        for bar in response.json() or []:
            bar_day = datetime.fromisoformat(bar["time"]).date()
            if bar_day <= at_day:
                return Decimal(bar["close"])
        if fail_on_missing:
            raise PriceFetchError([ticker], f"No Webull bar for {ticker} on or before {at_day}")
        return None

    ## account state

    def _get_paginated(self, fetch, items_keys: tuple[str, ...], cursor_key: str) -> list[dict]:
        """Walk one of Webull's cursor-paginated listings.

        The listings are inconsistent about casing (``has_next`` on positions,
        ``hasNext`` on orders) and omit the item list entirely when a page is
        empty, so both the page key and the continuation flag are matched
        leniently.
        """
        results: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            payload = call_with_retries(partial(fetch, cursor)).json() or {}
            page: list[dict] = []
            for key in items_keys:
                value = payload.get(key)
                if isinstance(value, list):
                    page = value
                    break
            results.extend(page)
            has_next = payload.get("has_next") or payload.get("hasNext")
            if not has_next or not page:
                return results
            cursor = page[-1].get(cursor_key)
            if not cursor:
                return results
        Logger.error(f"Stopped paginating Webull {items_keys[0]} after {MAX_PAGES} pages; results may be incomplete")
        return results

    def get_positions(self) -> list[dict]:
        try:
            return self._get_paginated(
                lambda cursor: self._api.account.get_account_position(
                    self._account_id,
                    page_size=MAX_PAGE_SIZE,
                    last_instrument_id=cursor,
                ),
                items_keys=("holdings",),
                cursor_key="instrument_id",
            )
        except self._server_exception as e:
            raise ConfigurationError(f"Could not fetch Webull positions: {e}")

    def get_portfolio(self) -> dict:
        try:
            response = call_with_retries(lambda: self._api.account.get_account_balance(self._account_id, DEFAULT_CURRENCY))
        except self._server_exception as e:
            raise ConfigurationError(f"Could not fetch Webull account balance: {e}")
        return response.json() or {}

    def get_open_orders(self) -> list[dict]:
        try:
            return self._get_paginated(
                lambda cursor: self._api.order.list_open_orders(
                    self._account_id,
                    page_size=MAX_PAGE_SIZE,
                    last_client_order_id=cursor,
                ),
                items_keys=("orders", "open_orders", "items"),
                cursor_key="client_order_id",
            )
        except self._server_exception as e:
            raise ConfigurationError(f"Could not fetch Webull open orders: {e}")

    def get_unsettled_instruments(self) -> set[str]:
        tickers = set()
        for order in self.get_open_orders():
            symbol = order.get("symbol")
            if symbol:
                tickers.add(_from_webull_symbol(symbol))
        return tickers

    def get_holdings(self) -> RealPortfolio:
        balance = self._get_cached_value(ObjectKey.ACCOUNT, callable=self.get_portfolio)
        positions = self._get_cached_value(ObjectKey.POSITIONS, callable=self.get_positions)
        unsettled = self._get_cached_value(ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments)

        # positions already carry a marked-to-market value, so holdings don't
        # need a second round of price lookups
        total_value = sum((Decimal(row.get("market_value") or 0) for row in positions), Decimal(0))
        holdings = []
        for row in positions:
            ticker = _from_webull_symbol(row["symbol"])
            value = Decimal(row.get("market_value") or 0)
            holdings.append(
                RealPortfolioElement(
                    ticker=ticker,
                    units=Decimal(row.get("qty") or 0),
                    value=Money(value=value),
                    weight=(value / total_value) if total_value else Decimal(0),
                    unsettled=ticker in unsettled,
                    appreciation=Money(value=Decimal(row.get("unrealized_profit_loss") or 0)),
                    dividends=Money(value=Decimal(0)),
                )
            )
        cash = Decimal(balance.get("total_cash_balance") or 0)
        return RealPortfolio(holdings=holdings, cash=Money(value=cash), provider=self)

    def get_per_ticker_profit_or_loss(self) -> dict[str, ProfitModel]:
        positions = self._get_cached_value(ObjectKey.POSITIONS, callable=self.get_positions)
        # Webull's OpenAPI exposes no dividend history, so appreciation is the
        # only component we can report; see _get_dividends.
        return {
            _from_webull_symbol(row["symbol"]): ProfitModel(
                appreciation=Money(value=Decimal(row.get("unrealized_profit_loss") or 0)),
                dividends=Money(value=Decimal(0)),
            )
            for row in positions
        }

    def _get_stock_info(self, ticker: str) -> dict:
        instrument = self._get_instrument(ticker)
        try:
            response = call_with_retries(lambda: self._api.instrument.get_instrument(_to_webull_symbol(ticker), instrument["category"]))
        except self._server_exception as e:
            Logger.error(f"Could not fetch Webull instrument detail for {ticker}: {e}")
            return {}
        rows = response.json() or []
        if not rows:
            return {}
        row = rows[0]
        return {
            "name": row.get("name"),
            "exchange": row.get("exchange_code"),
            "currency": row.get("currency"),
        }

    ## orders

    def _is_otc(self, ticker: str) -> bool:
        exchange = self._get_instrument(ticker)["exchange"].upper()
        return any(code in exchange for code in OTC_EXCHANGES)

    def _otc_limit_price(self, ticker: str, side: str) -> Decimal:
        price = self._get_instrument_price(ticker)
        if not price:
            raise OrderError(f"Failed to {side.lower()} {ticker}: no quote to set a limit price from")
        buying = side == "BUY"
        limit = price * (1 + OTC_LIMIT_BUFFER if buying else 1 - OTC_LIMIT_BUFFER)
        # round away from the quote so the buffer is never lost to rounding down
        tick = Decimal("0.01") if price >= 1 else Decimal("0.0001")
        return limit.quantize(tick, rounding=ROUND_UP if buying else ROUND_DOWN)

    @staticmethod
    def _order(ticker: str, qty: Decimal, side: str, terms: dict[str, Any]) -> dict[str, Any]:
        # orders go by symbol, which unlike the instrument id route also reaches OTC
        return {
            "combo_type": "NORMAL",
            "client_order_id": uuid.uuid4().hex,
            "symbol": _to_webull_symbol(ticker),
            "instrument_type": "EQUITY",
            "market": "US",
            "quantity": format(qty, "f"),
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            # market orders cannot route to extended hours
            "support_trading_session": "CORE",
            **terms,
        }

    def _place_order(self, ticker: str, qty: Decimal, side: str, limit_price: Decimal | None = None) -> None:
        terms: dict[str, Any] = {"order_type": "MARKET"}
        if limit_price:
            terms = {"order_type": "LIMIT", "limit_price": format(limit_price, "f")}
        if self._is_otc(ticker):
            # OTC orders are limit-only and in whole shares, so a fractional leg drops
            qty = Decimal(int(qty))
            if not qty:
                Logger.info(f"Skipping fractional OTC {side.lower()} of {ticker}")
                return
            if not limit_price:
                terms = {
                    "order_type": "LIMIT",
                    "limit_price": format(self._otc_limit_price(ticker, side), "f"),
                }
        order = self._order(ticker, qty, side, terms)
        try:
            response = call_with_retries(partial(self._api.order_v3.place_order, self._account_id, [order]))
        except self._server_exception as e:
            raise OrderError(f"Failed to {side.lower()} {qty} of {ticker}: {e}")
        if response.status_code != 200:
            raise OrderError(f"Failed to {side.lower()} {qty} of {ticker}: {response.text}")

    @staticmethod
    def _split_fractional(qty: Decimal) -> list[Decimal]:
        """Split a fractional quantity into whole and fractional legs.

        Webull books a fractional order above one share as two child orders,
        and rejects the combined quantity, so we submit the legs ourselves.
        """
        whole = int(qty)
        if whole and qty != whole:
            return [Decimal(whole), qty - whole]
        return [qty]

    def buy_instrument(
        self,
        ticker: str,
        qty: Decimal,
        value: Money | None = None,
        limit_price: Decimal | None = None,
    ) -> bool:
        # orders go by quantity, so a value below the share increment buys nothing
        if not qty:
            Logger.info(f"Skipping {ticker}: {value} is less than the smallest share increment")
            return False
        for leg in self._split_fractional(qty):
            self._place_order(ticker, leg, "BUY", limit_price)
        return True

    def sell_instrument(
        self,
        ticker: str,
        qty: Decimal,
        value: Money | None = None,
        limit_price: Decimal | None = None,
    ) -> bool:
        for leg in self._split_fractional(qty):
            self._place_order(ticker, leg, "SELL", limit_price)
        return True

    ## unsupported by the official API

    def get_transactions(self) -> list[Transaction]:
        raise NotImplementedError("Webull's OpenAPI only exposes the current day's orders for US " "accounts, so a complete transaction history cannot be built.")

    def _get_dividends(self) -> dict[str, Money]:
        raise NotImplementedError("Webull's OpenAPI has no dividend endpoint; dividend history is " "unavailable for this provider.")

    def get_dividend_details(self, start: datetime | None = None) -> list[DividendResult]:
        raise NotImplementedError("Webull's OpenAPI has no dividend endpoint; dividend history is " "unavailable for this provider.")


class WebullPaperProvider(WebullProvider):
    """Retained for backwards compatibility; Webull's OpenAPI has no paper mode."""

    PROVIDER = ProviderType.WEBULL_PAPER

    def __init__(self, *args: Any, **kwargs: Any):
        raise ConfigurationError("Webull's official OpenAPI does not offer paper trading. Use " "WebullProvider against a live account, or PaperAlpacaProvider for " "a paper account.")
