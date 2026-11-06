import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from py_portfolio_index.exceptions import ConfigurationError, OrderError
from py_portfolio_index.models import Money
from py_portfolio_index.portfolio_providers.alpaca_v2 import AlpacaProvider
from py_portfolio_index.portfolio_providers.moomoo import MooMooProvider
from py_portfolio_index.portfolio_providers.schwab import SchwabProvider


def alpaca(monkeypatch) -> tuple[AlpacaProvider, list]:
    provider = AlpacaProvider(key_id="key", secret_key="secret")
    submitted: list = []
    monkeypatch.setattr(provider.trading_client, "submit_order", lambda order_data: submitted.append(order_data))
    return provider, submitted


def test_alpaca_sells_shares_rather_than_an_estimated_value(monkeypatch):
    provider, submitted = alpaca(monkeypatch)
    provider.sell_instrument("AAA", Decimal("258.261425"), Money(value=14819))
    assert submitted[0].qty == 258.261425
    assert submitted[0].notional is None
    assert submitted[0].side.value == "sell"


def test_alpaca_value_only_sell_is_notional(monkeypatch):
    provider, submitted = alpaca(monkeypatch)
    provider.sell_instrument("AAA", Decimal(0), Money(value=50))
    assert submitted[0].qty is None
    assert submitted[0].notional == 50


@pytest.fixture
def moomoo_sdk(monkeypatch) -> SimpleNamespace:
    """The SDK is an optional extra, so CI doesn't have it."""
    sdk = SimpleNamespace(
        RET_OK=0,
        OrderType=SimpleNamespace(MARKET="MARKET"),
        TrdSide=SimpleNamespace(BUY="BUY", SELL="SELL"),
    )
    monkeypatch.setitem(sys.modules, "moomoo", sdk)
    return sdk


def moomoo(sdk: SimpleNamespace) -> tuple[MooMooProvider, list]:
    provider = MooMooProvider.__new__(MooMooProvider)
    placed: list = []

    def place_order(**kwargs):
        placed.append(kwargs)
        return sdk.RET_OK, "ok"

    provider._trade_provider = SimpleNamespace(place_order=place_order)  # type: ignore[attr-defined]
    provider.last_unlocked = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    provider._check_order_rate_limit = lambda: None  # type: ignore[method-assign]
    provider._record_order_attempt = lambda: None  # type: ignore[method-assign]
    return provider, placed


def test_moomoo_sell_is_a_market_sell_of_the_shares(moomoo_sdk):
    provider, placed = moomoo(moomoo_sdk)
    assert provider.sell_instrument("AAA", Decimal(766), Money(value=27000)) is True
    assert placed[0]["trd_side"] == moomoo_sdk.TrdSide.SELL
    assert placed[0]["order_type"] == moomoo_sdk.OrderType.MARKET
    assert placed[0]["qty"] == 766
    assert placed[0]["code"] == "US.AAA"
    assert placed[0]["price"] == 0.0


def test_moomoo_buy_still_buys(moomoo_sdk):
    provider, placed = moomoo(moomoo_sdk)
    provider.buy_instrument("AAA", Decimal(2))
    assert placed[0]["trd_side"] == moomoo_sdk.TrdSide.BUY


def test_moomoo_sell_needs_a_quantity(moomoo_sdk):
    provider, placed = moomoo(moomoo_sdk)
    with pytest.raises(OrderError):
        provider.sell_instrument("AAA", Decimal(0), Money(value=10))
    assert placed == []


def schwab(error: Exception | None = None) -> tuple[SchwabProvider, list]:
    provider = SchwabProvider.__new__(SchwabProvider)
    specs: list = []

    def place_order(account_hash, order_spec):
        if error:
            raise error
        specs.append(order_spec)
        return "response"

    provider._account_hash = "hash"  # type: ignore[attr-defined]
    provider._provider = SimpleNamespace(place_order=place_order)  # type: ignore[attr-defined]
    provider._utils = SimpleNamespace(extract_order_id=lambda order: "1")  # type: ignore[attr-defined]
    return provider, specs


def test_schwab_sell_is_a_whole_share_market_sell():
    provider, specs = schwab()
    assert provider.sell_instrument("AAA", Decimal("28.9"), Money(value=1600)) is True
    leg = specs[0]["orderLegCollection"][0]
    assert leg["instruction"] == "SELL"
    assert float(leg["quantity"]) == 28
    assert specs[0]["orderType"] == "MARKET"


def test_schwab_refuses_a_fraction_of_a_share():
    provider, specs = schwab()
    with pytest.raises(OrderError):
        provider.sell_instrument("AAA", Decimal("0.4"), Money(value=10))
    assert specs == []


def test_schwab_sell_reports_failure_as_an_order_error():
    provider, _ = schwab(RuntimeError("rejected"))
    with pytest.raises(OrderError):
        provider.sell_instrument("AAA", Decimal(1))


def test_schwab_sell_flags_an_expired_session(monkeypatch):
    import py_portfolio_index.portfolio_providers.schwab as module

    monkeypatch.setattr(module, "is_auth_error", lambda e: True)
    provider, _ = schwab(RuntimeError("401"))
    with pytest.raises(ConfigurationError):
        provider.sell_instrument("AAA", Decimal(1))
