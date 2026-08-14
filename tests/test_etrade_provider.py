from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from py_portfolio_index.enums import OrderType
from py_portfolio_index.exceptions import (
    ConfigurationError,
    OrderError,
    PriceFetchError,
)
from py_portfolio_index.portfolio_providers import etrade as etrade_provider
from py_portfolio_index.portfolio_providers.etrade import ETradeProvider
from py_portfolio_index.portfolio_providers.helpers import etrade as etrade_helper


## auth helper


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeOAuth1Session:
    """Stands in for requests_oauthlib.OAuth1Session in both auth legs."""

    renew_status = 200

    def __init__(
        self,
        client_key,
        client_secret=None,
        callback_uri=None,
        resource_owner_key=None,
        resource_owner_secret=None,
    ):
        self.client_key = client_key
        self.callback_uri = callback_uri
        self.resource_owner_key = resource_owner_key
        self.resource_owner_secret = resource_owner_secret

    def fetch_request_token(self, url):
        assert url == etrade_helper.REQUEST_TOKEN_URL
        return {"oauth_token": "req-token", "oauth_token_secret": "req-secret"}

    def fetch_access_token(self, url, verifier=None):
        assert url == etrade_helper.ACCESS_TOKEN_URL
        self.verifier = verifier
        return {"oauth_token": "access-token", "oauth_token_secret": "access-secret"}

    def get(self, url, **kwargs):
        assert url == etrade_helper.RENEW_TOKEN_URL
        return FakeResponse(status_code=self.renew_status)


@pytest.fixture
def isolated_token_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(etrade_helper, "user_cache_dir", lambda *args, **kwargs: str(tmp_path))
    monkeypatch.setattr(etrade_helper, "_oauth1_session_class", lambda: FakeOAuth1Session)
    FakeOAuth1Session.renew_status = 200
    return tmp_path


def _utc_stamp(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_token_is_current_same_eastern_day():
    token = {"created": _utc_stamp(2026, 8, 11, 13, 0)}
    now = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    assert etrade_helper.token_is_current(token, now=now)


def test_token_is_current_expires_at_eastern_midnight():
    # created 22:00 Eastern on Aug 10; by Eastern morning of Aug 11 it is dead,
    # even though both instants share the Aug 11 UTC date
    token = {"created": _utc_stamp(2026, 8, 11, 2, 0)}
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    assert not etrade_helper.token_is_current(token, now=now)


def test_token_is_current_requires_created():
    assert not etrade_helper.token_is_current({})
    assert not etrade_helper.token_is_current({"created": "yesterday"})


def test_token_cache_round_trip(isolated_token_cache):
    token = {
        "oauth_token": "tok",
        "oauth_token_secret": "secret",
        "created": datetime.now(tz=timezone.utc).timestamp(),
    }
    etrade_helper.save_token(token, sandbox=True)
    assert etrade_helper.load_cached_token(sandbox=True) == token
    # sandbox and production tokens are cached independently
    assert etrade_helper.load_cached_token(sandbox=False) is None
    etrade_helper.clear_cached_token(sandbox=True)
    assert etrade_helper.load_cached_token(sandbox=True) is None


def test_stale_token_is_discarded_on_load(isolated_token_cache):
    etrade_helper.save_token(
        {"oauth_token": "tok", "oauth_token_secret": "secret", "created": _utc_stamp(2020, 1, 1)},
        sandbox=True,
    )
    assert etrade_helper.load_cached_token(sandbox=True) is None
    assert not etrade_helper.token_path(sandbox=True).exists()


def test_interactive_login_flow(isolated_token_cache):
    seen_urls = []

    def verifier_func(url):
        seen_urls.append(url)
        return " code123 "

    session = etrade_helper.get_authenticated_session("key", "secret", sandbox=True, verifier_func=verifier_func)

    assert seen_urls == [f"{etrade_helper.AUTHORIZE_URL}?key=key&token=req-token"]
    assert session.resource_owner_key == "access-token"
    assert session.resource_owner_secret == "access-secret"
    cached = etrade_helper.load_cached_token(sandbox=True)
    assert cached["oauth_token"] == "access-token"


def test_cached_token_is_renewed_not_reauthorized(isolated_token_cache):
    etrade_helper.save_token(
        {
            "oauth_token": "tok",
            "oauth_token_secret": "secret",
            "created": datetime.now(tz=timezone.utc).timestamp(),
        },
        sandbox=True,
    )

    def verifier_func(url):
        raise AssertionError("should not re-run the authorization flow")

    session = etrade_helper.get_authenticated_session("key", "secret", sandbox=True, verifier_func=verifier_func)
    assert session.resource_owner_key == "tok"


def test_unrenewable_token_falls_back_to_login(isolated_token_cache):
    FakeOAuth1Session.renew_status = 401
    etrade_helper.save_token(
        {
            "oauth_token": "tok",
            "oauth_token_secret": "secret",
            "created": datetime.now(tz=timezone.utc).timestamp(),
        },
        sandbox=True,
    )
    session = etrade_helper.get_authenticated_session("key", "secret", sandbox=True, verifier_func=lambda url: "code")
    assert session.resource_owner_key == "access-token"


def test_non_interactive_without_token_raises(isolated_token_cache):
    with pytest.raises(ConfigurationError):
        etrade_helper.get_authenticated_session("key", "secret", sandbox=True, interactive=False)


def test_create_login_context_with_valid_cache_returns_none(isolated_token_cache):
    etrade_helper.save_token(
        {
            "oauth_token": "tok",
            "oauth_token_secret": "secret",
            "created": datetime.now(tz=timezone.utc).timestamp(),
        },
        sandbox=True,
    )
    assert etrade_helper.create_login_context("key", "secret", sandbox=True) is None


def test_split_flow_authorization(isolated_token_cache):
    context = etrade_helper.create_login_context("key", "secret", sandbox=True)
    assert context is not None
    assert context.authorization_url == f"{etrade_helper.AUTHORIZE_URL}?key=key&token=req-token"
    # default flow is out-of-band until a callback is registered with support
    assert context.flow.callback_uri == "oob"
    # no token is cached until the verifier comes back
    assert etrade_helper.load_cached_token(sandbox=True) is None

    etrade_helper.complete_authorization(context, " code123 ")
    assert context.flow.verifier == "code123"
    cached = etrade_helper.load_cached_token(sandbox=True)
    assert cached["oauth_token"] == "access-token"


def test_split_flow_with_registered_callback(isolated_token_cache):
    context = etrade_helper.create_login_context("key", "secret", sandbox=True, callback_url="http://localhost:3042/public/etrade/callback")
    assert context.flow.callback_uri == "http://localhost:3042/public/etrade/callback"


def test_complete_authorization_requires_verifier(isolated_token_cache):
    context = etrade_helper.create_login_context("key", "secret", sandbox=True)
    with pytest.raises(ConfigurationError):
        etrade_helper.complete_authorization(context, "  ")


## provider


ACCOUNT_LIST = {
    "AccountListResponse": {
        "Accounts": {
            "Account": [
                {"accountId": "111", "accountIdKey": "key-111", "accountStatus": "ACTIVE"},
                {"accountId": "222", "accountIdKey": "key-222", "accountStatus": "CLOSED"},
            ]
        }
    }
}

PORTFOLIO = {
    "PortfolioResponse": {
        "AccountPortfolio": [
            {
                "Position": [
                    {
                        "Product": {"symbol": "AAPL"},
                        "quantity": 10,
                        "marketValue": 1500.0,
                        "totalGainLoss": 100.0,
                    },
                    {
                        "Product": {"symbol": "GOOG"},
                        "quantity": 5,
                        "marketValue": 500.0,
                        "totalGainLoss": -50.0,
                    },
                ]
            }
        ]
    }
}

BALANCE = {"BalanceResponse": {"Computed": {"cashAvailableForInvestment": 250.5}}}

OPEN_ORDERS = {"OrdersResponse": {"Order": [{"OrderDetail": [{"Instrument": [{"Product": {"symbol": "GOOG"}}]}]}]}}

QUOTES = {
    "QuoteResponse": {
        "QuoteData": [
            {
                "Product": {"symbol": "AAPL"},
                "All": {"lastTrade": 150.25, "companyName": "APPLE INC"},
            }
        ]
    }
}

PREVIEW = {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 123}]}}
PLACE = {"PlaceOrderResponse": {"OrderIds": [{"orderId": 5}]}}

# 2025-08-07 00:00:00 UTC
TRANSACTION_DATE_MS = 1754524800000

TRANSACTIONS = {
    "TransactionListResponse": {
        "Transaction": [
            {
                "transactionType": "Bought",
                "transactionDate": TRANSACTION_DATE_MS,
                "transactionId": 1,
                "brokerage": {"Product": {"symbol": "AAPL"}, "quantity": 2, "price": 100.5},
            },
            {
                "transactionType": "Qualified Dividend",
                "transactionDate": TRANSACTION_DATE_MS,
                "transactionId": 2,
                "amount": 12.34,
                "brokerage": {"Product": {"symbol": "AAPL"}},
            },
            {
                "transactionType": "Interest",
                "transactionDate": TRANSACTION_DATE_MS,
                "transactionId": 3,
                "amount": 0.5,
                "brokerage": {},
            },
        ],
        "moreTransactions": False,
    }
}


class FakeApiSession:
    """Answers provider REST calls with canned payloads, recording each call."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None):
        self.calls.append((method, url, params, json))
        routes = {
            "/v1/accounts/list.json": ACCOUNT_LIST,
            "/portfolio.json": PORTFOLIO,
            "/balance.json": BALANCE,
            "/orders.json": OPEN_ORDERS,
            "/orders/preview.json": PREVIEW,
            "/orders/place.json": PLACE,
            "/transactions.json": TRANSACTIONS,
            "/v1/market/quote/": QUOTES,
        }
        for fragment, payload in routes.items():
            if fragment in url:
                return FakeResponse(payload=payload)
        return FakeResponse(status_code=404, text=f"no fake route for {url}")


@pytest.fixture
def provider(monkeypatch):
    session = FakeApiSession()
    monkeypatch.setattr(etrade_provider, "get_authenticated_session", lambda *args, **kwargs: session)
    instance = ETradeProvider(api_key="key", api_secret="secret", sandbox=True)
    return instance


def test_resolves_first_active_account(provider):
    assert provider._account_id_key == "key-111"


def test_unknown_account_id_raises(monkeypatch):
    monkeypatch.setattr(etrade_provider, "get_authenticated_session", lambda *args, **kwargs: FakeApiSession())
    with pytest.raises(ConfigurationError, match="not available"):
        ETradeProvider(api_key="key", api_secret="secret", sandbox=True, account_id="999")


def test_account_id_matches_either_identifier(monkeypatch):
    monkeypatch.setattr(etrade_provider, "get_authenticated_session", lambda *args, **kwargs: FakeApiSession())
    by_id = ETradeProvider(api_key="key", api_secret="secret", sandbox=True, account_id="111")
    assert by_id._account_id_key == "key-111"


def test_get_holdings(provider):
    holdings = provider.get_holdings()
    assert holdings.cash.value == Decimal("250.5")
    aapl = holdings.get_holding("AAPL")
    assert aapl.units == Decimal("10")
    assert aapl.value.value == Decimal("1500")
    assert aapl.weight == Decimal("0.75")
    assert not aapl.unsettled
    # GOOG has an open order against it
    assert holdings.get_holding("GOOG").unsettled


def test_get_per_ticker_profit_or_loss(provider):
    pl = provider.get_per_ticker_profit_or_loss()
    assert pl["AAPL"].appreciation.value == Decimal("100")
    assert pl["AAPL"].dividends.value == Decimal("12.34")
    assert pl["GOOG"].appreciation.value == Decimal("-50")


def test_get_instrument_prices(provider):
    assert provider._get_instrument_price("AAPL") == Decimal("150.25")
    with pytest.raises(PriceFetchError):
        provider._get_instrument_prices(["AAPL", "ZZZZ"])
    prices = provider._get_instrument_prices(["AAPL", "ZZZZ"], fail_on_missing=False)
    assert prices["ZZZZ"] is None


def test_historical_prices_unsupported(provider):
    with pytest.raises(NotImplementedError):
        provider._get_instrument_price("AAPL", at_day=date(2026, 1, 2))


def test_buy_previews_then_places(provider):
    assert provider.buy_instrument("AAPL", Decimal("2"))
    order_calls = [call for call in provider._session.calls if "/orders/" in call[1]]
    assert [call[1].split("/orders/")[-1] for call in order_calls] == [
        "preview.json",
        "place.json",
    ]
    place_body = order_calls[1][3]["PlaceOrderRequest"]
    assert place_body["PreviewIds"] == [{"previewId": 123}]
    instrument = place_body["Order"][0]["Instrument"][0]
    assert instrument["orderAction"] == "BUY"
    assert instrument["quantity"] == "2"


def test_fractional_orders_rejected(provider):
    with pytest.raises(OrderError, match="whole-share"):
        provider.buy_instrument("AAPL", Decimal("1.5"))


def test_get_transactions(provider):
    transactions = provider.get_transactions()
    assert len(transactions) == 1
    txn = transactions[0]
    assert txn.ticker == "AAPL"
    assert txn.type == OrderType.BUY
    assert txn.qty == Decimal("2")
    assert txn.unitPrice.value == Decimal("100.5")
    assert txn.date == date(2025, 8, 7)


def test_get_dividend_details(provider):
    dividends = provider.get_dividend_details()
    assert len(dividends) == 1
    assert dividends[0].ticker == "AAPL"
    assert dividends[0].amount.value == Decimal("12.34")
    assert dividends[0].external_id == "2"
