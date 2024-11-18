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
