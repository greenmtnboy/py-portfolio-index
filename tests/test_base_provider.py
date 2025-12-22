import pytest
from decimal import Decimal
from py_portfolio_index.models import Money, RealPortfolioElement
from py_portfolio_index.portfolio_providers.local_dict import LocalDictProvider
from py_portfolio_index.enums import RoundingStrategy

@pytest.fixture
def local_provider():
    try:
        holdings = [
            RealPortfolioElement(ticker="AAPL", units=Decimal("10"), value=Money(value=1500), weight=Decimal(0)),
            RealPortfolioElement(ticker="GOOG", units=Decimal("5"), value=Money(value=1000), weight=Decimal(0))
        ]
        price_dict = {
            "AAPL": Decimal("150.0"),
            "GOOG": Decimal("200.0"),
            "MSFT": Decimal("300.0")
        }
        return LocalDictProvider(holdings=holdings, price_dict=price_dict, cash=Money(value=10000))
    except Exception as e:
        print(f"Fixture setup failed: {e}")
        raise e

def test_calculate_buy_units_fractional(local_provider):
    # Test fractional shares
    to_buy_currency = Money(value=100.50)
    units = local_provider._calculate_buy_units(to_buy_currency, fractional_shares=True, rounding_strategy=RoundingStrategy.CLOSEST)
    assert units == Decimal("100.5000")

def test_calculate_buy_units_closest(local_provider):
    # Test rounding closest
    to_buy_currency = Money(value=100.6)
    units = local_provider._calculate_buy_units(to_buy_currency, fractional_shares=False, rounding_strategy=RoundingStrategy.CLOSEST)
    assert units == Money(value=101)

    to_buy_currency = Money(value=100.4)
    units = local_provider._calculate_buy_units(to_buy_currency, fractional_shares=False, rounding_strategy=RoundingStrategy.CLOSEST)
    assert units == Money(value=100)

def test_calculate_buy_units_floor(local_provider):
    # Test rounding floor
    to_buy_currency = Money(value=100.9)
    units = local_provider._calculate_buy_units(to_buy_currency, fractional_shares=False, rounding_strategy=RoundingStrategy.FLOOR)
    assert units == Money(value=100)

def test_calculate_buy_units_ceiling(local_provider):
    # Test rounding ceiling
    to_buy_currency = Money(value=100.1)
    units = local_provider._calculate_buy_units(to_buy_currency, fractional_shares=False, rounding_strategy=RoundingStrategy.CEILING)
    assert units == Money(value=101)

def test_purchase_ticker_value_dict_sufficient_funds(local_provider):
    to_buy = {"MSFT": Money(value=3000)}
    local_provider.purchase_ticker_value_dict(to_buy, purchasing_power=Money(value=5000))
    
    # Check if MSFT was bought
    holdings = local_provider.get_holdings()
    msft_holding = holdings.get_holding("MSFT")
    assert msft_holding is not None
    assert msft_holding.units == Decimal("10.0000")

def test_purchase_ticker_value_dict_insufficient_funds(local_provider):
    # Price of MSFT is 300
    # We want to buy 3000 worth (10 units)
    # But we only have 1500 purchasing power
    to_buy = {"MSFT": Money(value=3000)}
    local_provider.purchase_ticker_value_dict(to_buy, purchasing_power=Money(value=1500))
    
    holdings = local_provider.get_holdings()
    msft_holding = holdings.get_holding("MSFT")
    assert msft_holding is not None
    # Should have bought 5 units (1500 / 300)
    assert msft_holding.units == Decimal("5.0000")

def test_purchase_ticker_value_dict_skip_unsettled(local_provider):
    # Mock get_unsettled_instruments to return MSFT
    local_provider.get_unsettled_instruments = lambda: {"MSFT"}
    
    to_buy = {"MSFT": Money(value=3000)}
    local_provider.purchase_ticker_value_dict(to_buy, purchasing_power=Money(value=5000), ignore_unsettled=True)
    
    holdings = local_provider.get_holdings()
    msft_holding = holdings.get_holding("MSFT")
    assert msft_holding is None
