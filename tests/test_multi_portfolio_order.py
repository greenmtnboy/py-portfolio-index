from py_portfolio_index.models import (
    RealPortfolioElement,
    CompositePortfolio,
    Money,
    IdealPortfolio,
    IdealPortfolioElement,
)
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.portfolio_providers.local_dict import (
    LocalDictProvider,
    LocalDictNoPartialProvider,
)
from py_portfolio_index.operators import (
    generate_auto_target_size,
    generate_composite_order_plan,
    OrderElement,
    OrderType,
)
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.constants import Logger
from logging import StreamHandler, DEBUG

Logger.addHandler(StreamHandler())
Logger.setLevel(DEBUG)


def test_composite():
    provider1 = LocalDictProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=0.5, value=Money(value=50)),
            RealPortfolioElement(ticker="UNIL", units=1.0, value=Money(value=1000)),
        ],
        cash=Money(value=800),
    )
    provider2 = LocalDictNoPartialProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
        ],
        cash=Money(value=200),
    )

    provider1._price_dict = {"AAPL": 100, "UNIL": 1000, "MSFT": 33}
    provider2._price_dict = provider1._price_dict

    ideal_port = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )

    composite = CompositePortfolio(
        portfolios=[provider1.get_holdings(), provider2.get_holdings()]
    )

    expected_size = 2000

    composite_order_plan = generate_composite_order_plan(
        composite=composite,
        ideal=ideal_port,
        target_size=expected_size,
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
    )

    no_partial = composite_order_plan[LocalDictNoPartialProvider.PROVIDER]
    assert no_partial.to_buy == [
        OrderElement(
            ticker="MSFT",
            order_type=OrderType.BUY,
            value=None,
            qty=5,
            price=Money(value=33),
            provider=ProviderType.LOCAL_DICT_NO_PARTIAL,
        ),
        # OrderElement(ticker="AAPL", order_type= OrderType.BUY, value=None, qty=0, provider=Provider.LOCAL_DICT_NO_PARTIAL),
    ]
    partial = composite_order_plan[LocalDictProvider.PROVIDER]
    assert partial.to_buy == [
        OrderElement(
            ticker="AAPL",
            order_type=OrderType.BUY,
            value=Money(value="759.9999999999999644728632120"),
            qty=None,
            price=Money(value=100),
            provider=ProviderType.LOCAL_DICT,
        ),
    ]


def _build_composite_with_negative_cash():
    """One provider with positive cash, one with negative cash (e.g. margin debit)."""
    provider1 = LocalDictProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
        ],
        cash=Money(value=500),
    )
    provider2 = LocalDictNoPartialProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
        ],
        cash=Money(value=-200),
    )
    provider1._price_dict = {"AAPL": 100, "MSFT": 50}
    provider2._price_dict = provider1._price_dict
    return provider1, provider2, CompositePortfolio(
        portfolios=[provider1.get_holdings(), provider2.get_holdings()]
    )


def test_composite_cash_floors_negative_provider_at_zero():
    _, _, composite = _build_composite_with_negative_cash()
    # Without flooring this would be 500 + (-200) = 300.
    # With flooring it must be 500 + max(-200, 0) = 500.
    assert composite.cash == Money(value=500)


def test_generate_auto_target_size_floors_negative_cash():
    _, _, composite = _build_composite_with_negative_cash()
    ideal = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )
    # AAPL is held in both portfolios at $100 each = $200 in-portfolio value.
    # Cash, floored per provider, is 500 (negative provider contributes 0).
    target = generate_auto_target_size(real=composite, ideal=ideal)
    assert target == Money(value=700)


def test_composite_order_plan_skips_negative_cash_provider():
    provider1, provider2, composite = _build_composite_with_negative_cash()
    ideal = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )
    # Target large enough to consume all flooring-aware purchasing power.
    plan = generate_composite_order_plan(
        composite=composite,
        ideal=ideal,
        target_size=Money(value=2000),
        target_order_size=Money(value=1000),
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
    )
    # The negative-cash provider must not produce buys and must not have
    # consumed any of the target_order_size budget.
    assert LocalDictNoPartialProvider.PROVIDER not in plan or not plan[
        LocalDictNoPartialProvider.PROVIDER
    ].to_buy
    # The positive-cash provider should still receive its full available budget
    # (capped by the safety threshold of 0.95 applied in the planner).
    partial = plan[LocalDictProvider.PROVIDER]
    assert partial.to_buy, "expected the positive-cash provider to place orders"
    spent = sum(
        (o.value or Money(value=0)) for o in partial.to_buy
    )
    # Positive-cash provider has $500; min(500, 1000) capped by 0.95 safety = 475.
    assert spent <= Money(value=475)
    assert spent > Money(value=0)
