from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

import pytest

from py_portfolio_index.models import Money
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
