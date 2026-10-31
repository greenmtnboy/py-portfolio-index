from decimal import Decimal

from py_portfolio_index.bin import INDEXES
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.models import (
    CompositePortfolio,
    IdealPortfolio,
    IdealPortfolioElement,
    Money,
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.operators import (
    generate_composite_order_plan,
    generate_order_plan,
    quote_listings,
)
from py_portfolio_index.portfolio_providers.common import PriceCache
from py_portfolio_index.portfolio_providers.local_dict import LocalDictProvider


def airbus_and_apple() -> IdealPortfolio:
    return IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AIR.PA", weight=Decimal("0.5"), listings=["EADSY", "EADSF"]),
            IdealPortfolioElement(ticker="AAPL", weight=Decimal("0.5")),
        ]
    )


def us_quotes(tickers, date=None, fail_on_missing=True):
    """A US broker: no price for home-exchange symbols, like Schwab or Robinhood."""
    return {x: (None if "." in x else Decimal(50)) for x in tickers}


def test_buys_first_listing_the_provider_quotes():
    plan = generate_order_plan(
        RealPortfolio(holdings=[]),
        airbus_and_apple(),
        target_size=1000,
        price_fetcher=PriceCache(fetcher=us_quotes).get_prices,
    )

    assert {x.ticker: x.value for x in plan.to_buy} == {"EADSY": Money(value=500), "AAPL": Money(value=500)}


def test_rejected_home_symbol_does_not_fail_domestic_batch():
    def strict(tickers, date=None, fail_on_missing=True):
        # like a broker that rejects the whole request over one bad symbol
        if any("." in x for x in tickers):
            raise PriceFetchError(list(tickers), "invalid symbol")
        return {x: Decimal(50) for x in tickers}

    plan = generate_order_plan(
        RealPortfolio(holdings=[]),
        airbus_and_apple(),
        target_size=1000,
        price_fetcher=PriceCache(fetcher=strict).get_prices,
    )

    assert {x.ticker for x in plan.to_buy} == {"EADSY", "AAPL"}


def test_home_listing_preferred_when_it_quotes():
    plan = generate_order_plan(
        RealPortfolio(holdings=[]),
        airbus_and_apple(),
        target_size=1000,
        price_fetcher=PriceCache(fetcher=lambda tickers, date, fail_on_missing=True: {x: Decimal(50) for x in tickers}).get_prices,
    )

    assert {x.ticker for x in plan.to_buy} == {"AIR.PA", "AAPL"}


def test_known_symbols_skip_unlisted_listings_without_quoting():
    quoted: list[str] = []

    def record(tickers, date=None, fail_on_missing=True):
        quoted.extend(tickers)
        return {x: Decimal(50) for x in tickers}

    plan = generate_order_plan(
        RealPortfolio(holdings=[]),
        airbus_and_apple(),
        target_size=1000,
        price_fetcher=PriceCache(fetcher=record).get_prices,
        known_symbols={"AAPL", "EADSF"},
    )

    assert {x.ticker for x in plan.to_buy} == {"EADSF", "AAPL"}
    assert "AIR.PA" not in quoted and "EADSY" not in quoted


def test_holdings_in_any_listing_count():
    real = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="EADSY", units=10, value=Money(value=300)),
            RealPortfolioElement(ticker="AIR.PA", units=1, value=Money(value=200)),
        ]
    )

    plan = generate_order_plan(
        real,
        airbus_and_apple(),
        target_size=1000,
        price_fetcher=PriceCache(fetcher=us_quotes).get_prices,
    )

    assert {x.ticker: x.value for x in plan.to_buy} == {"AAPL": Money(value=500)}


def test_unquotable_element_is_skipped():
    ideal = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="RO.SW", weight=Decimal("0.5"), listings=["RHHBY"]),
            IdealPortfolioElement(ticker="AAPL", weight=Decimal("0.5")),
        ]
    )

    plan = generate_order_plan(
        RealPortfolio(holdings=[]),
        ideal,
        target_size=1000,
        buy_order=PurchaseStrategy.PEANUT_BUTTER,
        purchase_power=100,
        price_fetcher=PriceCache(fetcher=lambda tickers, date, fail_on_missing=True: {x: (Decimal(50) if x == "AAPL" else None) for x in tickers}).get_prices,
    )

    # the cash Roche can't use goes to Apple rather than sitting idle
    assert {x.ticker: x.value for x in plan.to_buy} == {"AAPL": Money(value=100)}


def test_composite_sells_the_listing_each_provider_holds():
    holder = LocalDictProvider(
        holdings=[RealPortfolioElement(ticker="EADSY", units=20, value=Money(value=1000))],
        price_dict={"EADSY": Decimal(50), "AAPL": Decimal(50)},
        cash=Money(value=100),
    )
    composite = CompositePortfolio([holder.get_holdings()])

    plan = generate_composite_order_plan(
        composite,
        airbus_and_apple(),
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
        target_size=1000,
        safety_threshold=Decimal(1),
        include_sell_orders=True,
    )

    sells = plan[holder.PROVIDER].to_sell
    assert [(x.ticker, x.qty) for x in sells] == [("EADSY", 10)]


def test_buys_count_against_later_providers_in_any_listing():
    ideal = airbus_and_apple()
    first = generate_order_plan(
        RealPortfolio(holdings=[]),
        ideal,
        target_size=1000,
        price_fetcher=PriceCache(fetcher=us_quotes).get_prices,
    )

    second = generate_order_plan(
        RealPortfolio(holdings=[]),
        ideal,
        target_size=1000,
        price_fetcher=PriceCache(fetcher=lambda tickers, date, fail_on_missing=True: {x: Decimal(50) for x in tickers}).get_prices,
        existing_orders=first.to_buy,
    )

    assert second.to_buy == []


def test_quote_listings_falls_through_to_last_listing():
    def fetch(tickers):
        return {x: (Decimal(1) if x == "C" else None) for x in tickers}

    assert quote_listings({"X": ["A", "B", "C"], "Y": ["A"]}, fetch) == {"X": ("C", Decimal(1))}


def test_exclusion_matches_adr():
    ideal = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="BATS.L", weight=Decimal("0.5"), listings=["BTI"]),
            IdealPortfolioElement(ticker="AAPL", weight=Decimal("0.5")),
        ]
    )

    ideal.exclude(["BTI"])

    assert [x.ticker for x in ideal.holdings] == ["AAPL"]
    assert ideal.contains("AAPL") and not ideal.contains("BTI")


def test_blended_index_carries_home_listings_and_adrs():
    blended = INDEXES["blended_world_60_40"]
    by_ticker = {x.ticker: x for x in blended.holdings}

    assert by_ticker["AIR.PA"].listings == ["EADSY"]
    assert "LVMUY" in by_ticker["MC.PA"].listings
    assert abs(sum(x.weight for x in blended.holdings) - 1) < Decimal("1e-6")
