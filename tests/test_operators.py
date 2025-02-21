from py_portfolio_index.operators import (
    generate_order_plan,
    generate_composite_order_plan,
    generate_auto_target_size,
)
from py_portfolio_index.models import (
    RealPortfolio,
    RealPortfolioElement,
    Money,
    IdealPortfolio,
    IdealPortfolioElement,
    CompositePortfolio,
)
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.portfolio_providers.local_dict import LocalDictProvider
from py_portfolio_index.portfolio_providers.common import PriceCache


def test_generate_order_plan():
    real_port = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100))
        ]
    )

    ideal_port = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )

    order_plan = generate_order_plan(
        real_port,
        ideal_port,
        target_size=1000,
        buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
        price_fetcher=PriceCache(
            fetcher=lambda tickers, date: {y: 100 for y in tickers},
        ).get_prices,
    )

    expected = {"AAPL": Money(value=400), "MSFT": Money(value=500)}

    for x in order_plan.to_buy:
        assert x.value == expected[x.ticker]


def test_generate_composite__order_plan():
    provider1 = LocalDictProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
            RealPortfolioElement(ticker="UNIL", units=1.0, value=Money(value=1000)),
        ],
        cash=Money(value=800),
    )
    provider2 = LocalDictProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
        ],
        cash=Money(value=0),
    )

    real_port1 = provider1.get_holdings()
    real_port2 = provider2.get_holdings()

    ideal_port = IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=0.5),
            IdealPortfolioElement(ticker="MSFT", weight=0.5),
        ]
    )
    children = [real_port1, real_port2]
    composite = CompositePortfolio(children)
    ideal = ideal_port

    auto_size = generate_auto_target_size(composite, ideal)
    assert auto_size == Money(value=1000), "auto portfolio size should be 10000"
    order_plan = generate_composite_order_plan(
        composite,
        ideal,
        target_size=auto_size,
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
        safety_threshold=0,
    )

    expected = {"AAPL": Money(value=300), "MSFT": Money(value=500)}
    assert len(order_plan.keys()) == 1, "order plan should have one provider key"
    for provider, order_plan in order_plan.items():
        for x in order_plan.to_buy:
            assert x.value == expected[x.ticker]
