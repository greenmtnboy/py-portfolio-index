from decimal import Decimal

from py_portfolio_index import INDEXES, STOCK_LISTS
from py_portfolio_index.models import IdealPortfolio, IdealPortfolioElement


def test_indexes():
    for key in INDEXES.keys:
        x = INDEXES[key]

        print(x.json())
    print(INDEXES.json())


def test_lists():
    for key in STOCK_LISTS.keys:
        STOCK_LISTS[key]

    print(STOCK_LISTS.json())


def test_funds_list():
    funds = set(STOCK_LISTS["funds"])
    for fund in ["SPY", "VTI", "QQQ", "DIA", "GLD", "IBIT"]:
        assert fund in funds
    # SPCX was a SPAC ETF ticker before SpaceX listed under it
    for stock in ["AAPL", "NVDA", "SPCX", "BLK", "FBRT"]:
        assert stock not in funds


def test_exclude_funds():
    ideal = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=Decimal("0.5")),
            IdealPortfolioElement(ticker="SPY", weight=Decimal("0.3")),
            IdealPortfolioElement(ticker="MSFT", weight=Decimal("0.2")),
        ]
    )
    ideal.exclude(STOCK_LISTS["funds"])
    weights = {h.ticker: h.weight for h in ideal.holdings}
    assert weights.keys() == {"AAPL", "MSFT"}
    assert abs(weights["AAPL"] - Decimal(5) / Decimal(7)) < Decimal("0.000001")


def test_blended_index_has_no_eu_ticker_collisions():
    """European home-exchange symbols must not land on unrelated US tickers."""
    us = {h.ticker: h.weight for h in INDEXES["total_market"].holdings}
    blended = {h.ticker: h.weight for h in INDEXES["blended_world_60_40"].holdings}
    us_share = blended["AAPL"] / us["AAPL"]
    # MC is LVMH in Paris and Moelis in New York; ROG Roche and Rogers; AIR Airbus and AAR
    for ticker in ["MC", "ROG", "OR", "DTE", "AIR", "AI", "EL", "CFR", "PRU", "TEN"]:
        assert abs(blended.get(ticker, Decimal(0)) - us.get(ticker, Decimal(0)) * us_share) < Decimal("0.0000001"), ticker
    assert "DIA" not in blended
    for ticker in ["ASML", "SAP", "NVO", "NVS", "SNY", "SAN"]:
        assert blended[ticker] > Decimal("0.003"), ticker
