from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

import pytest

from py_portfolio_index.enums import OrderType
from py_portfolio_index.exceptions import OrderError
from py_portfolio_index.models import Money, OrderElement
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.portfolio_providers.moomoo import MooMooProvider, _position_gain


def position(code="US.AAPL", **gains):
    return SimpleNamespace(code=code, qty=2, market_val=400, **gains)


@pytest.mark.parametrize(
    "gains,expected",
    [
        ({"unrealized_pl": 40.5, "pl_val": 99}, Decimal("40.5")),
        ({"unrealized_pl": "N/A", "pl_val": -12.25}, Decimal("-12.25")),
        ({"pl_val": 7}, Decimal(7)),
        ({"unrealized_pl": float("nan"), "pl_val": "N/A"}, Decimal(0)),
        ({}, Decimal(0)),
    ],
)
def test_position_gain(gains, expected):
    assert _position_gain(position(**gains)) == expected


def test_holdings_carry_appreciation():
    provider = MooMooProvider.__new__(MooMooProvider)
    provider._get_cached_value = lambda key, value=None, callable=None, **kwargs: callable()  # type: ignore[method-assign]
    provider._get_portfolio = lambda: SimpleNamespace(us_cash=10)  # type: ignore[method-assign]
    provider._get_positions = lambda: [position(unrealized_pl=40)]  # type: ignore[method-assign]
    provider._get_dividends = lambda: defaultdict(lambda: Money(value=0))  # type: ignore[method-assign]
    provider.get_unsettled_instruments = lambda: set()  # type: ignore[method-assign]

    holdings = provider.get_holdings()

    assert holdings.holdings[0].appreciation == Money(value=40)
    assert holdings.profit_and_loss.appreciation == Money(value=40)


class FakeQuotes:
    """Moomoo's quote context: it knows OTC symbols but serves no OTC quotes."""

    def __init__(self, exchanges):
        self.exchanges = exchanges

    def get_stock_basicinfo(self, market, sec_type, codes):
        import pandas as pd

        rows = [{"code": c, "exchange_type": self.exchanges[c[3:]]} for c in codes if c[3:] in self.exchanges]
        return 0, pd.DataFrame(rows, columns=["code", "exchange_type"])

    def subscribe(self, codes, sub_types, subscribe_push=False):
        return -1, f"Do not support OTC market data {codes[0][3:]}"


@pytest.fixture(autouse=True)
def moomoo_sdk(monkeypatch):
    # CI doesn't install the moomoo SDK; these tests only need its constants
    import sys

    sdk = SimpleNamespace(RET_OK=0, TrdSide=SimpleNamespace(BUY="BUY", SELL="SELL"))
    monkeypatch.setitem(sys.modules, "moomoo", sdk)
    return sdk


def order_provider(exchanges):
    provider = MooMooProvider.__new__(MooMooProvider)
    BaseProvider.__init__(provider)
    provider._quote_context = FakeQuotes(exchanges)
    placed = []
    provider._place_order = lambda ticker, qty, value, side, limit_price=None: (
        placed.append(  # type: ignore[method-assign]
            (ticker, qty, str(side), limit_price)
        )
        or True
    )
    return provider, placed


def test_otc_buy_is_a_limit_order_at_the_planned_price():
    provider, placed = order_provider({"TCEHY": "US_PINK"})

    # a planned order carries the price another broker quoted for it
    provider.handle_order_element(OrderElement(ticker="TCEHY", order_type=OrderType.BUY, qty=1, value=Money(value="50")))

    assert placed == [("TCEHY", Decimal(1), "BUY", Decimal("50.50"))]


def test_otc_sell_limit_sits_below_the_price():
    provider, placed = order_provider({"WLWHY": "US_PINK"})

    provider.handle_order_element(OrderElement(ticker="WLWHY", order_type=OrderType.SELL, qty=2, value=Money(value="1.70")))

    assert placed == [("WLWHY", Decimal(2), "SELL", Decimal("0.8415"))]


def test_listed_buy_stays_a_market_order():
    provider, placed = order_provider({"AAPL": "US_NASDAQ"})

    provider.buy_instrument("AAPL", Decimal(1), Money(value=200))

    assert placed == [("AAPL", Decimal(1), "BUY", None)]


def test_unquotable_order_without_a_planned_price_fails_cleanly():
    provider, placed = order_provider({"TCEHY": "US_PINK"})

    with pytest.raises(OrderError, match="No price found"):
        provider.handle_order_element(OrderElement(ticker="TCEHY", order_type=OrderType.BUY, qty=1, value=None))
    assert placed == []
