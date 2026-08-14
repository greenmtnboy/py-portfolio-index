from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from os import environ
from typing import Any, Dict, List, Optional, Set
import json
import re
import uuid

from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.constants import Logger, CACHE_DIR
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

# /market-data/snapshot accepts at most 100 symbols per call
SNAPSHOT_BATCH_SIZE = 100
# /account/positions and the order listings require page_size in [10, 100]
MAX_PAGE_SIZE = 100
# guard against paginating forever if the API keeps reporting another page
MAX_PAGES = 100
# /market-data/history-bar caps the bar count per request
MAX_HISTORY_BARS = 1200

# Webull files instruments under a category, and rejects a symbol looked up
# under the wrong one. US equities land in exactly one of these two.
US_EQUITY_CATEGORIES = ("US_STOCK", "US_ETF")

# Webull spells class shares with a space where this library uses a dot,
# e.g. BRK.B is "BRK B".
_SYMBOL_SEPARATOR = " "
_INVALID_SYMBOL_LIST = re.compile(r"\[(.*?)\]")


def _to_webull_symbol(ticker: str) -> str:
    return ticker.replace(".", _SYMBOL_SEPARATOR)


def _from_webull_symbol(symbol: str) -> str:
    return symbol.replace(_SYMBOL_SEPARATOR, ".")


def _parse_invalid_symbols(error: Exception) -> Set[str]:
    """Pull the rejected symbols out of an INVALID_SYMBOL error message.

    Webull answers a batch containing any unknown symbol with a 417 naming all
    of the offenders, e.g. ``The symbols does not exist in the category.
    [ZZZZ, DIA].`` - so one bad symbol fails the whole batch, and the message
    is the only way to learn which ones to drop or retry elsewhere.
    """
    match = _INVALID_SYMBOL_LIST.search(str(error))
    if not match:
        return set()
    return {part.strip() for part in match.group(1).split(",") if part.strip()}


class WebullProvider(BaseProvider):
    """Provider for interacting with stocks held in Webull.

    Uses the official Webull OpenAPI SDK. Generate an app key and secret from
    the Webull developer portal for your region and either pass them in or set
    WEBULL_API_KEY and WEBULL_API_SECRET.
    """

    PROVIDER = ProviderType.WEBULL
    SUPPORTS_BATCH_HISTORY = 0

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

        # symbol -> {"instrument_id": str, "category": str}
        self._local_instrument_cache: Dict[str, Dict[str, str]] = {}
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
        # corruption guard, and a guard against the pre-SDK cache format which
        # stored a bare instrument id per symbol
        if not isinstance(cached, dict) or not all(isinstance(v, dict) for v in cached.values()):
            cached = {}
        self._local_instrument_cache = cached

    def _save_local_instrument_cache(self):
        with open(self._cache_file(), "w") as f:
            json.dump(self._local_instrument_cache, f)

    ## instrument resolution

    def _snapshot_batch(self, symbols: List[str], category: str) -> tuple[Dict[str, dict], List[str]]:
        """Snapshot one batch, returning rows keyed by symbol plus any rejects.

        A single unrecognized symbol fails the entire request, so we peel the
        rejected symbols off the error and retry with the remainder.
        """
        remaining = list(symbols)
        rejected: List[str] = []
        while remaining:
            try:
                response = call_with_retries(lambda: self._api.market_data.get_snapshot(remaining, category))
            except self._server_exception as e:
                if getattr(e, "get_error_code", lambda: None)() != "INVALID_SYMBOL":
                    raise
                bad = _parse_invalid_symbols(e) & set(remaining)
                if not bad:
                    # can't tell which symbol webull disliked; treat all as bad
                    return {}, remaining
                rejected.extend(bad)
                remaining = [s for s in remaining if s not in bad]
                continue
            rows = {}
            for row in response.json() or []:
                # halted or delisted instruments come back as a bare id
                if "symbol" not in row:
                    continue
                rows[row["symbol"]] = row
            return rows, rejected
        return {}, rejected

    def _fetch_snapshots(self, symbols: List[str]) -> Dict[str, dict]:
        """Snapshot symbols, discovering and caching each one's category.

        Symbols with a known category are grouped so they cost one request per
        category; unknown ones are probed against each category in turn.
        """
        by_category: defaultdict[str, List[str]] = defaultdict(list)
        unknown: List[str] = []
        for ticker in symbols:
            symbol = _to_webull_symbol(ticker)
            cached = self._local_instrument_cache.get(ticker)
            if cached:
                by_category[cached["category"]].append(symbol)
            else:
                unknown.append(symbol)

        rows: Dict[str, dict] = {}
        found_categories: Dict[str, str] = {}

        for category, group in by_category.items():
            for batch in divide_into_batches(group, SNAPSHOT_BATCH_SIZE):
                found, rejected = self._snapshot_batch(batch, category)
                rows.update(found)
                # a symbol can be recategorized (e.g. a fund converting); retry it
                unknown.extend(rejected)

        for category in US_EQUITY_CATEGORIES:
            if not unknown:
                break
            still_unknown: List[str] = []
            for batch in divide_into_batches(unknown, SNAPSHOT_BATCH_SIZE):
                found, rejected = self._snapshot_batch(batch, category)
                rows.update(found)
                found_categories.update({symbol: category for symbol in found})
                still_unknown.extend(rejected)
            unknown = still_unknown

        for symbol in unknown:
            Logger.error(f"Could not find a Webull instrument for {symbol}")

        if found_categories:
            for symbol, category in found_categories.items():
                row = rows[symbol]
                self._local_instrument_cache[_from_webull_symbol(symbol)] = {
                    "instrument_id": str(row["instrumentId"]),
                    "category": category,
                }
            self._save_local_instrument_cache()

        return {_from_webull_symbol(symbol): row for symbol, row in rows.items()}

    def _get_instrument(self, ticker: str) -> Dict[str, str]:
        """Resolve a ticker to its Webull instrument id and category."""
        cached = self._local_instrument_cache.get(ticker)
        if cached:
            return cached
        self._fetch_snapshots([ticker])
        cached = self._local_instrument_cache.get(ticker)
        if not cached:
            raise OrderError(f"Could not find a Webull instrument for {ticker}")
        return cached

    ## pricing

    def _get_instrument_prices(
        self,
        tickers: List[str],
        at_day: Optional[date] = None,
        fail_on_missing: bool = True,
    ) -> Dict[str, Optional[Decimal]]:
        if at_day:
            prices: Dict[str, Optional[Decimal]] = {}
            for ticker in tickers:
                prices[ticker] = self._get_historical_price(ticker, at_day, fail_on_missing=fail_on_missing)
            return prices

        rows = self._fetch_snapshots(tickers)
        missing = [ticker for ticker in tickers if ticker not in rows]
        if missing and fail_on_missing:
            raise PriceFetchError(missing, f"No Webull price found for {missing}")
        return {ticker: (Decimal(rows[ticker]["price"]) if rows.get(ticker, {}).get("price") is not None else None) for ticker in tickers}

    def _get_instrument_price(self, ticker: str, at_day: Optional[date] = None, fail_on_missing: bool = True) -> Optional[Decimal]:
        return self._get_instrument_prices([ticker], at_day=at_day, fail_on_missing=fail_on_missing)[ticker]

    def _get_historical_price(self, ticker: str, at_day: date, fail_on_missing: bool = True) -> Optional[Decimal]:
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

    def _get_paginated(self, fetch, items_keys: tuple[str, ...], cursor_key: str) -> List[dict]:
        """Walk one of Webull's cursor-paginated listings.

        The listings are inconsistent about casing (``has_next`` on positions,
        ``hasNext`` on orders) and omit the item list entirely when a page is
        empty, so both the page key and the continuation flag are matched
        leniently.
        """
        results: List[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            payload = call_with_retries(lambda: fetch(cursor)).json() or {}
            page: List[dict] = []
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

    def get_positions(self) -> List[dict]:
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

    def get_open_orders(self) -> List[dict]:
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

    def get_unsettled_instruments(self) -> Set[str]:
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

    def get_per_ticker_profit_or_loss(self) -> Dict[str, ProfitModel]:
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

    def _place_order(self, ticker: str, qty: Decimal, side: str) -> None:
        instrument = self._get_instrument(ticker)
        try:
            response = call_with_retries(
                lambda: self._api.order.place_order(
                    account_id=self._account_id,
                    instrument_id=instrument["instrument_id"],
                    client_order_id=uuid.uuid4().hex,
                    side=side,
                    qty=format(qty, "f"),
                    order_type="MARKET",
                    tif="DAY",
                    # market orders cannot route to extended hours
                    extended_hours_trading=False,
                )
            )
        except self._server_exception as e:
            raise OrderError(f"Failed to {side.lower()} {qty} of {ticker}: {e}")
        if response.status_code != 200:
            raise OrderError(f"Failed to {side.lower()} {qty} of {ticker}: {response.text}")

    @staticmethod
    def _split_fractional(qty: Decimal) -> List[Decimal]:
        """Split a fractional quantity into whole and fractional legs.

        Webull books a fractional order above one share as two child orders,
        and rejects the combined quantity, so we submit the legs ourselves.
        """
        whole = int(qty)
        if whole and qty != whole:
            return [Decimal(whole), qty - whole]
        return [qty]

    def buy_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None) -> bool:
        for leg in self._split_fractional(qty):
            self._place_order(ticker, leg, "BUY")
        return True

    def sell_instrument(self, ticker: str, qty: Decimal, value: Optional[Money] = None) -> bool:
        for leg in self._split_fractional(qty):
            self._place_order(ticker, leg, "SELL")
        return True

    ## unsupported by the official API

    def get_transactions(self) -> List[Transaction]:
        raise NotImplementedError("Webull's OpenAPI only exposes the current day's orders for US " "accounts, so a complete transaction history cannot be built.")

    def _get_dividends(self) -> Dict[str, Money]:
        raise NotImplementedError("Webull's OpenAPI has no dividend endpoint; dividend history is " "unavailable for this provider.")

    def get_dividend_details(self, start: datetime | None = None) -> List[DividendResult]:
        raise NotImplementedError("Webull's OpenAPI has no dividend endpoint; dividend history is " "unavailable for this provider.")


class WebullPaperProvider(WebullProvider):
    """Retained for backwards compatibility; Webull's OpenAPI has no paper mode."""

    PROVIDER = ProviderType.WEBULL_PAPER

    def __init__(self, *args: Any, **kwargs: Any):
        raise ConfigurationError("Webull's official OpenAPI does not offer paper trading. Use " "WebullProvider against a live account, or PaperAlpacaProvider for " "a paper account.")
