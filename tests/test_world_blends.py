from decimal import Decimal

import pytest

from py_portfolio_index.bin import INDEXES

BLENDS = {
    "blended_world_60_40": Decimal("0.6"),
    "blended_world_70_30": Decimal("0.7"),
    "blended_world_80_20": Decimal("0.8"),
}


@pytest.mark.parametrize("name,us_ratio", BLENDS.items())
def test_blend_splits_us_and_world(name, us_ratio):
    holdings = INDEXES[name].holdings
    assert abs(sum(x.weight for x in holdings) - 1) < Decimal("1e-6")

    # total_market sums to a little under 1 after delisted tickers are dropped
    us_holdings = INDEXES["total_market"].holdings
    us_total = sum(h.weight for h in us_holdings)
    us = {h.ticker: h.weight / us_total for h in us_holdings}
    blended = {h.ticker: h.weight for h in holdings}
    assert abs(blended["AAPL"] / us["AAPL"] - us_ratio) < Decimal("1e-6")

    # the sleeve reaches beyond Europe, each through a US line
    by_symbol = {symbol: h for h in holdings for symbol in h.symbols}
    for home, us_line in [("2330.TW", "TSM"), ("0700.HK", "TCEHY"), ("7203.T", "TM"), ("RY.TO", "RY"), ("BHP.AX", "BHP"), ("VALE3.SA", "VALE"), ("AIR.PA", "EADSY")]:
        assert by_symbol[home] is by_symbol[us_line], home


def test_world_ex_us_is_all_reachable():
    world = INDEXES["world_ex_us"].holdings
    assert len(world) > 900
    assert abs(sum(x.weight for x in world) - 1) < Decimal("1e-6")
    # a company with no US line can't be bought anywhere, so it is left out
    unreachable = [x.ticker for x in world if "." in x.ticker and not any("." not in s for s in x.listings)]
    assert unreachable == []
