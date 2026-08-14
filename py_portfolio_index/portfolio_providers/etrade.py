import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from os import environ
from typing import Any

from py_portfolio_index.common import divide_into_batches
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import Currency, OrderType, ProviderType
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
from py_portfolio_index.portfolio_providers.helpers.etrade import (
    get_authenticated_session,
)

PROD_API_BASE = "https://api.etrade.com"
SANDBOX_API_BASE = "https://apisb.etrade.com"

# /v1/market/quote accepts at most 25 symbols per call
QUOTE_BATCH_SIZE = 25
# count cap on the paginated account listings
MAX_PAGE_SIZE = 50
# guard against paginating forever if the API keeps reporting another page
MAX_PAGES = 100

# E*TRADE's clientOrderId is limited to 20 alphanumeric characters
CLIENT_ORDER_ID_LENGTH = 20

# E*TRADE rate limits are per consumer key and quite strict in the sandbox;
# retry throttled calls a few times before giving up
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BASE_DELAY_SECONDS = 0.5

TRANSACTION_TYPE_MAP = {
    "Bought": OrderType.BUY,
    "Sold": OrderType.SELL,
}

_TRUTHY = ("1", "true", "yes", "on")


class ETradeAPIError(Exception):
    """A non-2xx response from the E*TRADE API."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"E*TRADE API returned {status_code}: {message}")
        self.status_code = status_code


def _ensure_list(value: Any) -> list[dict]:
    """Some E*TRADE payloads collapse single-element arrays to a bare object."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    # JSON numerics arrive as floats; stringify to avoid binary float artifacts
    return Decimal(str(value))


def _ms_to_date(epoch_ms: Any) -> date:
    return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).date()


class ETradeProvider(BaseProvider):
    """Provider for interacting with stocks held in E*TRADE.

    Uses the E*TRADE v1 REST API over OAuth 1.0a. Create an API key and secret
    from the E*TRADE developer portal and either pass them in or set
    ETRADE_API_KEY and ETRADE_API_SECRET. The first use (and the first use
    each day, since E*TRADE tokens expire at midnight US Eastern) requires an
    interactive authorization: a browser opens to E*TRADE and the user pastes
    back a verification code. Set sandbox=True or ETRADE_SANDBOX=true to
    target the sandbox environment with sandbox consumer keys.
    """

    PROVIDER = ProviderType.ETRADE
    SUPPORTS_BATCH_HISTORY = 0
    # E*TRADE's API only accepts whole-share equity orders
    SUPPORTS_FRACTIONAL_SHARES = False
    MAX_ORDER_DECIMALS = 0

    API_KEY_ENV = "ETRADE_API_KEY"
    API_SECRET_ENV = "ETRADE_API_SECRET"
    ACCOUNT_ID_ENV = "ETRADE_ACCOUNT_ID"
    SANDBOX_ENV = "ETRADE_SANDBOX"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        account_id: str | None = None,
        sandbox: bool | None = None,
        external_auth: bool = False,
        verifier_func: Callable[[str], str] | None = None,
    ):
        if not api_key:
            api_key = environ.get(self.API_KEY_ENV, None)
        if not api_secret:
            api_secret = environ.get(self.API_SECRET_ENV, None)
        if not account_id:
            account_id = environ.get(self.ACCOUNT_ID_ENV, None)
        if sandbox is None:
            sandbox = environ.get(self.SANDBOX_ENV, "").lower() in _TRUTHY
        if not (api_key and api_secret):
            raise ConfigurationError("Must provide api_key and api_secret arguments or set environment " f"variables {self.API_KEY_ENV} and {self.API_SECRET_ENV}")

        self._sandbox = sandbox
        self._base_url = SANDBOX_API_BASE if sandbox else PROD_API_BASE
        self._session = get_authenticated_session(
            api_key,
            api_secret,
            sandbox=sandbox,
            interactive=not external_auth,
            verifier_func=verifier_func,
        )
        self._account_id_key = self._resolve_account_id(account_id)

        BaseProvider.__init__(self)

    ## plumbing

    def _request(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json_body,
                headers={"Accept": "application/json"},
            )
            if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else RATE_LIMIT_BASE_DELAY_SECONDS * (2**attempt)
            Logger.info(f"E*TRADE rate limited {method} {path}; retrying in {delay}s")
            time.sleep(delay)
        # empty listings (no open orders, no transactions) come back as 204
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            raise ETradeAPIError(response.status_code, response.text or response.reason or "")
        return response.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body: dict) -> dict:
        return self._request("POST", path, json_body=json_body)

    ## setup

    def _resolve_account_id(self, account_id: str | None) -> str:
        """Map an accountId or accountIdKey to the accountIdKey the API wants."""
        try:
            payload = self._get("/v1/accounts/list.json")
        except ETradeAPIError as e:
            raise ConfigurationError(f"Could not list E*TRADE accounts for these credentials: {e}")
        accounts = _ensure_list(payload.get("AccountListResponse", {}).get("Accounts", {}).get("Account"))
        active = [row for row in accounts if row.get("accountStatus") != "CLOSED" and row.get("accountIdKey")]
        if not active:
            raise ConfigurationError("These E*TRADE credentials have no active accounts.")
        if account_id:
            for row in active:
                if account_id in (row.get("accountId"), row.get("accountIdKey")):
                    return row["accountIdKey"]
            raise ConfigurationError(f"Account {account_id} is not available to these credentials; " f"found {[row.get('accountId') for row in active]}")
        if len(active) > 1:
            Logger.warning(f"E*TRADE credentials cover multiple accounts {[row.get('accountId') for row in active]}; " f"defaulting to {active[0].get('accountId')}. Pass account_id or set " f"{self.ACCOUNT_ID_ENV} to choose a different one.")
        return active[0]["accountIdKey"]

    ## pricing

    def _get_instrument_prices(
        self,
        tickers: list[str],
        at_day: date | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal | None]:
        if at_day:
            raise NotImplementedError("E*TRADE's API has no historical price endpoint; only live quotes are available.")
        prices: dict[str, Decimal | None] = {}
        for batch in divide_into_batches(list(tickers), QUOTE_BATCH_SIZE):
            try:
                payload = self._get(f"/v1/market/quote/{','.join(batch)}.json")
            except ETradeAPIError as e:
                raise PriceFetchError(batch, f"Could not fetch E*TRADE quotes for {batch}: {e}")
            for row in _ensure_list(payload.get("QuoteResponse", {}).get("QuoteData")):
                symbol = row.get("Product", {}).get("symbol")
                last = row.get("All", {}).get("lastTrade")
                if symbol and last is not None:
                    prices[symbol] = _to_decimal(last)
        # unknown symbols are simply absent from QuoteData (details land in
        # QuoteResponse.Messages), so reconcile against what was asked for
        missing = [ticker for ticker in tickers if ticker not in prices]
        if missing and fail_on_missing:
            raise PriceFetchError(missing, f"No E*TRADE price found for {missing}")
        return {ticker: prices.get(ticker) for ticker in tickers}

    def _get_instrument_price(self, ticker: str, at_day: date | None = None, fail_on_missing: bool = True) -> Decimal | None:
        return self._get_instrument_prices([ticker], at_day=at_day, fail_on_missing=fail_on_missing)[ticker]

    ## account state

    def get_positions(self) -> list[dict]:
        results: list[dict] = []
        page = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"count": MAX_PAGE_SIZE}
            if page:
                params["pageNumber"] = page
            try:
                payload = self._get(f"/v1/accounts/{self._account_id_key}/portfolio.json", params)
            except ETradeAPIError as e:
                if e.status_code == 401:
                    raise ConfigurationError(f"Could not fetch E*TRADE positions: {e}")
                raise
            portfolios = _ensure_list(payload.get("PortfolioResponse", {}).get("AccountPortfolio"))
            if not portfolios:
                return results
            results.extend(_ensure_list(portfolios[0].get("Position")))
            page = portfolios[0].get("nextPageNo")
            if not page:
                return results
        Logger.error(f"Stopped paginating E*TRADE positions after {MAX_PAGES} pages; results may be incomplete")
        return results

    def get_portfolio(self) -> dict:
        try:
            payload = self._get(
                f"/v1/accounts/{self._account_id_key}/balance.json",
                {"instType": "BROKERAGE", "realTimeNAV": "true"},
            )
        except ETradeAPIError as e:
            if e.status_code == 401:
                raise ConfigurationError(f"Could not fetch E*TRADE account balance: {e}")
            raise
        return payload.get("BalanceResponse", {})

    def get_open_orders(self) -> list[dict]:
        results: list[dict] = []
        marker = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"status": "OPEN", "count": MAX_PAGE_SIZE}
            if marker:
                params["marker"] = marker
            try:
                payload = self._get(f"/v1/accounts/{self._account_id_key}/orders.json", params)
            except ETradeAPIError as e:
                if e.status_code == 401:
                    raise ConfigurationError(f"Could not fetch E*TRADE open orders: {e}")
                raise
            response = payload.get("OrdersResponse", {})
            results.extend(_ensure_list(response.get("Order")))
            marker = response.get("marker")
            if not marker:
                return results
        Logger.error(f"Stopped paginating E*TRADE orders after {MAX_PAGES} pages; results may be incomplete")
        return results

    def get_unsettled_instruments(self) -> set[str]:
        tickers = set()
        for order in self.get_open_orders():
            for detail in _ensure_list(order.get("OrderDetail")):
                for instrument in _ensure_list(detail.get("Instrument")):
                    symbol = instrument.get("Product", {}).get("symbol")
                    if symbol:
                        tickers.add(symbol)
        return tickers

    def get_holdings(self) -> RealPortfolio:
        balance = self._get_cached_value(ObjectKey.ACCOUNT, callable=self.get_portfolio)
        positions = self._get_cached_value(ObjectKey.POSITIONS, callable=self.get_positions)
        unsettled = self._get_cached_value(ObjectKey.UNSETTLED, callable=self.get_unsettled_instruments)

        # positions already carry a marked-to-market value, so holdings don't
        # need a second round of price lookups
        total_value = sum((_to_decimal(row.get("marketValue")) for row in positions), Decimal(0))
        holdings = []
        for row in positions:
            ticker = row.get("Product", {}).get("symbol")
            if not ticker:
                continue
            value = _to_decimal(row.get("marketValue"))
            holdings.append(
                RealPortfolioElement(
                    ticker=ticker,
                    units=_to_decimal(row.get("quantity")),
                    value=Money(value=value),
                    weight=(value / total_value) if total_value else Decimal(0),
                    unsettled=ticker in unsettled,
                    appreciation=Money(value=_to_decimal(row.get("totalGainLoss"))),
                    dividends=Money(value=Decimal(0)),
                )
            )
        computed = balance.get("Computed", {})
        cash = _to_decimal(computed.get("cashAvailableForInvestment") if computed.get("cashAvailableForInvestment") is not None else computed.get("netCash"))
        return RealPortfolio(holdings=holdings, cash=Money(value=cash), provider=self)

    def get_per_ticker_profit_or_loss(self) -> dict[str, ProfitModel]:
        positions = self._get_cached_value(ObjectKey.POSITIONS, callable=self.get_positions)
        dividends = self.get_dividend_history()
        results = {}
        for row in positions:
            ticker = row.get("Product", {}).get("symbol")
            if not ticker:
                continue
            results[ticker] = ProfitModel(
                appreciation=Money(value=_to_decimal(row.get("totalGainLoss"))),
                dividends=dividends.get(ticker, Money(value=0)),
            )
        # dividends from positions since sold still count toward P&L
        for ticker, amount in dividends.items():
            if ticker not in results:
                results[ticker] = ProfitModel(appreciation=Money(value=0), dividends=amount)
        return results

    def _get_stock_info(self, ticker: str) -> dict:
        try:
            payload = self._get(f"/v1/market/quote/{ticker}.json")
        except ETradeAPIError as e:
            Logger.error(f"Could not fetch E*TRADE quote detail for {ticker}: {e}")
            return {}
        rows = _ensure_list(payload.get("QuoteResponse", {}).get("QuoteData"))
        if not rows:
            return {}
        detail = rows[0].get("All", {})
        return {
            "name": detail.get("companyName"),
            "exchange": detail.get("primaryExchange"),
        }

    ## orders

    def _place_order(self, ticker: str, qty: Decimal, side: str) -> None:
        if qty != int(qty):
            raise OrderError(f"E*TRADE only supports whole-share orders; got {qty} of {ticker}")
        order = {
            "orderType": "EQ",
            "clientOrderId": uuid.uuid4().hex[:CLIENT_ORDER_ID_LENGTH],
            "Order": [
                {
                    "allOrNone": "false",
                    "priceType": "MARKET",
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "Instrument": [
                        {
                            "Product": {"securityType": "EQ", "symbol": ticker},
                            "orderAction": side,
                            "quantityType": "QUANTITY",
                            "quantity": str(int(qty)),
                        }
                    ],
                }
            ],
        }
        # orders are a two-step protocol: preview, then place with the preview ids
        try:
            preview = self._post(
                f"/v1/accounts/{self._account_id_key}/orders/preview.json",
                {"PreviewOrderRequest": order},
            )
        except ETradeAPIError as e:
            raise OrderError(f"Failed to preview {side.lower()} of {qty} {ticker}: {e}")
        preview_ids = _ensure_list(preview.get("PreviewOrderResponse", {}).get("PreviewIds"))
        if not preview_ids:
            raise OrderError(f"E*TRADE returned no preview id for {side.lower()} of {qty} {ticker}: {preview}")
        placement = {**order, "PreviewIds": preview_ids}
        try:
            self._post(
                f"/v1/accounts/{self._account_id_key}/orders/place.json",
                {"PlaceOrderRequest": placement},
            )
        except ETradeAPIError as e:
            raise OrderError(f"Failed to {side.lower()} {qty} of {ticker}: {e}")

    def buy_instrument(self, ticker: str, qty: Decimal, value: Money | None = None) -> bool:
        self._place_order(ticker, qty, "BUY")
        return True

    def sell_instrument(self, ticker: str, qty: Decimal, value: Money | None = None) -> bool:
        self._place_order(ticker, qty, "SELL")
        return True

    ## transactions and dividends

    def _get_raw_transactions(self) -> list[dict]:
        results: list[dict] = []
        marker = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"count": MAX_PAGE_SIZE}
            if marker:
                params["marker"] = marker
            try:
                payload = self._get(f"/v1/accounts/{self._account_id_key}/transactions.json", params)
            except ETradeAPIError as e:
                if e.status_code == 401:
                    raise ConfigurationError(f"Could not fetch E*TRADE transactions: {e}")
                raise
            response = payload.get("TransactionListResponse", {})
            results.extend(_ensure_list(response.get("Transaction")))
            marker = response.get("marker")
            if not marker or not response.get("moreTransactions", True):
                return results
        Logger.error(f"Stopped paginating E*TRADE transactions after {MAX_PAGES} pages; results may be incomplete")
        return results

    def get_transactions(self) -> list[Transaction]:
        results = []
        for row in self._get_cached_value(ObjectKey.MISC, value="transactions", callable=self._get_raw_transactions):
            order_type = TRANSACTION_TYPE_MAP.get(row.get("transactionType"))
            if not order_type:
                continue
            brokerage = row.get("brokerage", {})
            symbol = brokerage.get("Product", {}).get("symbol")
            if not symbol:
                continue
            results.append(
                Transaction(
                    date=_ms_to_date(row["transactionDate"]),
                    ticker=symbol,
                    qty=abs(_to_decimal(brokerage.get("quantity"))),
                    type=order_type,
                    unitPrice=Money(value=_to_decimal(brokerage.get("price"))),
                    currency=Currency.USD,
                )
            )
        return results

    def get_dividend_details(self, start: datetime | None = None) -> list[DividendResult]:
        results = []
        for row in self._get_cached_value(ObjectKey.DIVIDENDS_DETAIL, callable=self._get_raw_transactions):
            # covers "Dividend" and "Qualified Dividend" style labels
            if "dividend" not in str(row.get("transactionType", "")).lower():
                continue
            symbol = row.get("brokerage", {}).get("Product", {}).get("symbol")
            if not symbol:
                continue
            event_date = _ms_to_date(row["transactionDate"])
            if start and event_date < start.date():
                continue
            results.append(
                DividendResult(
                    ticker=symbol,
                    date=event_date,
                    amount=Money(value=_to_decimal(row.get("amount"))),
                    provider=self.PROVIDER,
                    external_id=str(row.get("transactionId")),
                )
            )
        return results

    def _get_dividends(self) -> dict[str, Money]:
        totals: defaultdict[str, Money] = defaultdict(lambda: Money(value=0))
        for item in self.get_dividend_details():
            totals[item.ticker] += item.amount
        return dict(totals)
