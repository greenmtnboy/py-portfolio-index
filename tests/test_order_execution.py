"""Planning and executing sells, merging plans, and keeping quote calls down."""

from decimal import Decimal

import pytest

from py_portfolio_index.enums import OrderType, ProviderType, PurchaseStrategy, RoundingStrategy
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.models import (
    CompositePortfolio,
    IdealPortfolio,
    IdealPortfolioElement,
    Money,
    OrderElement,
    OrderPlan,
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.operators import (
    cap_sells_to_holdings,
    compare_portfolios,
    generate_composite_order_plan,
    generate_order_plan,
    purchase_composite_order_plan,
    round_int_with_strategy,
    round_with_strategy,
)
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.portfolio_providers.local_dict import (
    LocalDictNoPartialProvider,
    LocalDictProvider,
)


def even_ideal(*tickers: str) -> IdealPortfolio:
    return IdealPortfolio(holdings=[IdealPortfolioElement(ticker=t, weight=Decimal(1) / len(tickers)) for t in tickers])


def holding(ticker: str, value, units=1) -> RealPortfolioElement:
    return RealPortfolioElement(ticker=ticker, units=units, value=Money(value=value))


class QuoteLog:
    def __init__(self, prices: dict[str, Decimal]):
        self.prices = prices
        self.requested: list[list[str]] = []

    def __call__(self, tickers):
        self.requested.append(list(tickers))
        return {t: self.prices[t] for t in tickers}


def test_only_tickers_that_could_be_ordered_are_quoted():
    ideal = even_ideal("AAA", "BBB", "CCC", "DDD")
    real = RealPortfolio(
        holdings=[
            holding("AAA", 100),  # $150 short
            holding("BBB", "249.50"),  # $0.50 short: below the minimum order
            holding("CCC", 250),  # on target
            holding("DDD", 400),  # overweight
        ]
    )
    quotes = QuoteLog(dict.fromkeys(["AAA", "BBB", "CCC", "DDD"], Decimal(10)))
    plan = generate_order_plan(real, ideal, quotes, target_size=1000, purchase_power=1000)
    assert quotes.requested == [["AAA"]]
    assert [(o.ticker, o.value) for o in plan.to_buy] == [("AAA", Money(value=150))]


def test_nothing_to_buy_skips_the_quote_call():
    ideal = even_ideal("AAA", "BBB")
    real = RealPortfolio(holdings=[holding("AAA", 500), holding("BBB", 500)])
    quotes = QuoteLog({})
    plan = generate_order_plan(real, ideal, quotes, target_size=1000, purchase_power=1000)
    assert quotes.requested == []
    assert plan.to_buy == []


def test_sell_orders_are_whole_shares_within_the_excess():
    ideal = even_ideal("AAA", "BBB", "CCC")
    real = RealPortfolio(
        holdings=[
            holding("AAA", 500, units=50),  # $166.67 over
            holding("BBB", "334.50", units=33),  # $1.17 over: below the minimum order
            holding("CCC", 100, units=10),
        ]
    )
    quotes = QuoteLog(dict.fromkeys(["AAA", "BBB", "CCC"], Decimal(10)))
    plan = generate_order_plan(real, ideal, quotes, target_size=1000, purchase_power=0, include_sell_orders=True)
    assert [(o.ticker, o.qty, o.value) for o in plan.to_sell] == [("AAA", 16, Money(value=160))]
    assert all(o.order_type == OrderType.SELL for o in plan.to_sell)


def test_sell_orders_are_left_out_unless_asked_for():
    ideal = even_ideal("AAA", "BBB")
    real = RealPortfolio(holdings=[holding("AAA", 900, units=90), holding("BBB", 100, units=10)])
    quotes = QuoteLog(dict.fromkeys(["AAA", "BBB"], Decimal(10)))
    plan = generate_order_plan(real, ideal, quotes, target_size=1000, purchase_power=100)
    assert plan.to_sell == []
    assert quotes.requested == [["BBB"]]


def test_price_failure_retry_keeps_sell_orders():
    ideal = even_ideal("AAA", "BBB", "CCC")
    real = RealPortfolio(holdings=[holding("AAA", 600, units=60), holding("BBB", 100, units=10)])

    def quotes(tickers):
        if "CCC" in tickers:
            raise PriceFetchError(["CCC"], ValueError("no quote"))
        return dict.fromkeys(tickers, Decimal(10))

    plan = generate_order_plan(real, ideal, quotes, target_size=900, purchase_power=500, include_sell_orders=True)
    # the retry used to drop include_sell_orders, so the AAA sell vanished
    assert [o.ticker for o in plan.to_sell] == ["AAA"]
    assert [o.ticker for o in plan.to_buy] == ["BBB"]


def test_composite_counts_planned_sells_as_reducing_a_holding():
    # one provider sells AAA down; the next must not think AAA is still overweight
    # by the full amount, nor treat the sale as a purchase
    sell_order = OrderElement(ticker="AAA", order_type=OrderType.SELL, value=Money(value=300), qty=30, price=Money(value=10))
    ideal = even_ideal("AAA", "BBB")
    real = RealPortfolio(holdings=[holding("AAA", 800, units=80), holding("BBB", 200, units=20)])
    quotes = QuoteLog(dict.fromkeys(["AAA", "BBB"], Decimal(10)))
    plan = generate_order_plan(real, ideal, quotes, target_size=1000, purchase_power=0, existing_orders=[sell_order], include_sell_orders=True)
    assert [(o.ticker, o.qty) for o in plan.to_sell] == []
    plan = generate_order_plan(
        real,
        ideal,
        quotes,
        target_size=1000,
        purchase_power=0,
        existing_orders=[sell_order.model_copy(update={"qty": 10, "value": Money(value=100)})],
        include_sell_orders=True,
    )
    assert [(o.ticker, o.qty) for o in plan.to_sell] == [("AAA", 20)]


def test_sells_are_capped_to_what_the_provider_holds():
    port = RealPortfolio(holdings=[holding("AAA", 50, units=Decimal("5.5"))])
    sells = [
        OrderElement(ticker="AAA", order_type=OrderType.SELL, value=Money(value=100), qty=10, price=Money(value=10)),
        OrderElement(ticker="ZZZ", order_type=OrderType.SELL, value=Money(value=100), qty=10, price=Money(value=10)),
    ]
    assert [(o.ticker, o.qty, o.value) for o in cap_sells_to_holdings(sells, port)] == [("AAA", 5, Money(value=50))]


def test_composite_sells_only_from_the_provider_holding_the_stock():
    holder = LocalDictProvider(holdings=[holding("AAA", 800, units=80)], price_dict={"AAA": Decimal(10), "BBB": Decimal(10)}, cash=Money(value=0))
    other = LocalDictNoPartialProvider(holdings=[holding("BBB", 200, units=20)], price_dict={"AAA": Decimal(10), "BBB": Decimal(10)}, cash=Money(value=100))
    composite = CompositePortfolio([holder.get_holdings(), other.get_holdings()])
    plans = generate_composite_order_plan(
        composite,
        even_ideal("AAA", "BBB"),
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
        target_size=Money(value=1000),
        include_sell_orders=True,
    )
    # the provider with cash buys, but can't sell AAA it doesn't hold
    assert plans[ProviderType.LOCAL_DICT_NO_PARTIAL].to_sell == []
    assert [(o.ticker, o.qty) for o in plans[ProviderType.LOCAL_DICT_NO_PARTIAL].to_buy] == [("BBB", 9)]
    # the holder has no cash, but selling needs none
    assert plans[ProviderType.LOCAL_DICT].to_buy == []
    assert [(o.ticker, o.qty) for o in plans[ProviderType.LOCAL_DICT].to_sell] == [("AAA", 30)]


def test_zero_purchase_power_buys_nothing():
    ideal = even_ideal("AAA", "BBB")
    real = RealPortfolio(holdings=[holding("AAA", 100)])
    plan = generate_order_plan(real, ideal, QuoteLog(dict.fromkeys(["AAA", "BBB"], Decimal(10))), target_size=1000, purchase_power=0)
    # zero used to fall through to "no cap" and spend the whole target
    assert plan.to_buy == []


def test_order_plans_merge_orders_for_the_same_ticker():
    def buy(ticker, value):
        return OrderElement(ticker=ticker, order_type=OrderType.BUY, value=Money(value=value), qty=None, provider=ProviderType.LOCAL_DICT)

    plan = OrderPlan(to_buy=[buy("AAA", 10)], to_sell=[])
    plan += OrderPlan(to_buy=[buy("AAA", 5), buy("BBB", 7)], to_sell=[])
    # the merged AAA order used to be computed and thrown away
    assert [(o.ticker, o.value, o.provider) for o in plan.to_buy] == [
        ("AAA", Money(value=15), ProviderType.LOCAL_DICT),
        ("BBB", Money(value=7), ProviderType.LOCAL_DICT),
    ]


def test_qty_orders_merge_by_quantity():
    a = OrderElement(ticker="AAA", order_type=OrderType.SELL, value=Money(value=20), qty=2, price=Money(value=10))
    b = OrderElement(ticker="AAA", order_type=OrderType.SELL, value=Money(value=30), qty=3, price=Money(value=10))
    merged = a + b
    assert (merged.qty, merged.value, merged.price) == (5, Money(value=50), Money(value=10))


def test_orders_for_different_providers_do_not_merge():
    a = OrderElement(ticker="AAA", order_type=OrderType.BUY, value=Money(value=1), qty=None, provider=ProviderType.LOCAL_DICT)
    b = OrderElement(ticker="AAA", order_type=OrderType.BUY, value=Money(value=1), qty=None, provider=ProviderType.ALPACA)
    with pytest.raises(ValueError):
        a + b


class RecordingProvider(BaseProvider):
    def __init__(self):
        super().__init__()
        self.quotes = 0
        self.placed: list[tuple[str, str, Decimal]] = []

    def _get_instrument_price(self, ticker, at_day=None, fail_on_missing=True):
        self.quotes += 1
        return Decimal(10)

    def buy_instrument(self, ticker, qty, value=None):
        self.placed.append(("BUY", ticker, qty))
        return True

    def sell_instrument(self, ticker, qty, value=None):
        self.placed.append(("SELL", ticker, qty))
        return True

    def get_unsettled_instruments(self):
        return {"UNSETTLED"}


def plan_with_sell() -> OrderPlan:
    return OrderPlan(
        to_buy=[OrderElement(ticker="BBB", order_type=OrderType.BUY, value=Money(value=50), qty=None)],
        to_sell=[
            OrderElement(ticker="AAA", order_type=OrderType.SELL, value=Money(value=30), qty=3, price=Money(value=10)),
            OrderElement(ticker="UNSETTLED", order_type=OrderType.SELL, value=Money(value=30), qty=3, price=Money(value=10)),
        ],
    )


def test_planned_sells_are_executed_first_when_asked():
    provider = RecordingProvider()
    provider.purchase_order_plan(plan_with_sell(), include_sell_orders=True)
    # sells used to be read only from to_buy, so none were ever placed
    assert provider.placed == [("SELL", "AAA", Decimal(3)), ("BUY", "BBB", Decimal(5))]


def test_planned_sells_are_skipped_by_default():
    provider = RecordingProvider()
    provider.purchase_order_plan(plan_with_sell())
    assert provider.placed == [("BUY", "BBB", Decimal(5))]


def test_value_orders_quote_once():
    provider = RecordingProvider()
    provider._price_cache.default_timeout = -1  # every lookup goes to the broker
    provider.handle_order_element(OrderElement(ticker="BBB", order_type=OrderType.BUY, value=Money(value=50), qty=None))
    assert provider.quotes == 1


def test_plan_only_value_dict_purchase_places_nothing():
    provider = RecordingProvider()
    # used to raise UnboundLocalError
    provider.purchase_ticker_value_dict({"AAA": Money(value=100)}, purchasing_power=1000, plan_only=True)
    assert provider.placed == []


def test_composite_holding_lookup_sums_across_providers():
    composite = CompositePortfolio([RealPortfolio(holdings=[holding("AAA", 10)]), RealPortfolio(holdings=[holding("AAA", 5), holding("BBB", 1)])])
    assert composite.get_holding("AAA").value == Money(value=15)
    assert composite.get_holding("BBB").value == Money(value=1)
    assert composite.get_holding("CCC") is None


def test_compare_portfolios_splits_over_and_underweight():
    ideal = even_ideal("AAA", "BBB", "CCC")
    real = RealPortfolio(holdings=[holding("AAA", 600), holding("BBB", 300)])
    to_buy, to_sell = compare_portfolios(real, ideal, target_size=900)
    assert to_buy == {"CCC": Money(value=300)}
    assert to_sell == {"AAA": Money(value=300)}
    with pytest.raises(ValueError):
        compare_portfolios(real, ideal, buy_order=PurchaseStrategy.PEANUT_BUTTER)


@pytest.mark.parametrize(
    "strategy, expected",
    [(RoundingStrategy.CLOSEST, 3), (RoundingStrategy.FLOOR, 2), (RoundingStrategy.CEILING, 3)],
)
def test_rounding_strategies(strategy, expected):
    assert round_int_with_strategy(Decimal("2.6"), strategy) == expected
    assert round_with_strategy(Decimal("2.6"), strategy) == Money(value=expected)


def test_composite_plan_executes_against_each_provider():
    prices = {"AAA": Decimal(10), "BBB": Decimal(25)}
    whole = LocalDictNoPartialProvider(holdings=[holding("AAA", 100, units=10)], price_dict=dict(prices), cash=Money(value=100))
    fractional = LocalDictProvider(holdings=[], price_dict=dict(prices), cash=Money(value=200))
    composite = CompositePortfolio([whole.get_holdings(), fractional.get_holdings()])
    plans = generate_composite_order_plan(
        composite,
        even_ideal("AAA", "BBB"),
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
        target_size=Money(value=400),
        safety_threshold=Decimal(1),
    )
    # whole shares first: 4 BBB for $100; the fractional account fills the rest
    assert [(o.ticker, o.qty) for o in plans[ProviderType.LOCAL_DICT_NO_PARTIAL].to_buy] == [("BBB", 4)]
    assert [(o.ticker, o.value) for o in plans[ProviderType.LOCAL_DICT].to_buy] == [("AAA", Money(value=100)), ("BBB", Money(value=100))]
    assert purchase_composite_order_plan(plans, [whole, fractional])
    assert whole.get_holdings().get_holding("BBB").units == 4
    assert fractional.get_holdings().get_holding("AAA").value == Money(value=100)


def test_failed_orders_can_be_skipped():
    class FailingProvider(RecordingProvider):
        def buy_instrument(self, ticker, qty, value=None):
            if ticker == "BAD":
                raise RuntimeError("rejected")
            return super().buy_instrument(ticker, qty, value)

    plan = OrderPlan(
        to_buy=[
            OrderElement(ticker="BAD", order_type=OrderType.BUY, value=Money(value=50), qty=None),
            OrderElement(ticker="BBB", order_type=OrderType.BUY, value=Money(value=50), qty=None),
        ],
        to_sell=[],
    )
    with pytest.raises(RuntimeError):
        FailingProvider().purchase_order_plan(plan)
    provider = FailingProvider()
    provider.purchase_order_plan(plan, skip_errored_stocks=True)
    assert provider.placed == [("BUY", "BBB", Decimal(5))]
