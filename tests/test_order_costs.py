"""Regression tests for buy orders that spent more than the plan asked for."""

import random
from decimal import Decimal

import pytest

from py_portfolio_index.common import round_down_to_place
from py_portfolio_index.enums import OrderType, PurchaseStrategy
from py_portfolio_index.exceptions import OrderError
from py_portfolio_index.models import (
    IdealPortfolio,
    IdealPortfolioElement,
    Money,
    OrderElement,
    RealPortfolio,
    RealPortfolioElement,
)
from py_portfolio_index.operators import MIN_ORDER_MONEY, generate_order_plan
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.portfolio_providers.robinhood import RobinhoodProvider
from py_portfolio_index.portfolio_providers.webull import WebullProvider

STRATEGIES = [
    PurchaseStrategy.LARGEST_DIFF_FIRST,
    PurchaseStrategy.CHEAPEST_FIRST,
    PurchaseStrategy.PEANUT_BUTTER,
]


def fixed_prices(prices: dict[str, Decimal]):
    return lambda tickers: {t: prices[t] for t in tickers}


def scenario(seed: int, count: int = 200, size: int = 10_000, invested: float = 0.9):
    """A portfolio that roughly tracks an index, with some cash to spend."""
    rng = random.Random(seed)
    raw = [rng.random() ** 3 for _ in range(count)]
    ideal = IdealPortfolio(holdings=[IdealPortfolioElement(ticker=f"T{i}", weight=Decimal(w / sum(raw))) for i, w in enumerate(raw)])
    real = RealPortfolio(
        holdings=[
            RealPortfolioElement(
                ticker=h.ticker,
                units=1,
                value=Money(value=float(h.weight) * size * invested * rng.uniform(0.8, 1.2)),
            )
            for h in ideal.holdings
        ]
    )
    prices = {h.ticker: Decimal(str(round(rng.lognormvariate(4, 1.2), 2))) for h in ideal.holdings}
    gaps = {h.ticker: h.weight * size - real.get_holding(h.ticker).value.decimal for h in ideal.holdings}
    return real, ideal, prices, gaps


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("fractional", [True, False])
@pytest.mark.parametrize("purchase_power", [50, 200, 1000, 5000])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_orders_stay_within_gap_and_purchase_power(strategy, fractional, purchase_power, seed):
    real, ideal, prices, gaps = scenario(seed)
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices(prices),
        buy_order=strategy,
        target_size=10_000,
        purchase_power=purchase_power,
        fractional_shares=fractional,
    )
    total = Decimal(0)
    for order in plan.to_buy:
        value = order.inferred_value.decimal
        # never buy past the target weight: the $2 minimum used to inflate small gaps
        assert value <= gaps[order.ticker] + Decimal("0.000001"), order
        assert value >= MIN_ORDER_MONEY.decimal, order
        total += value
    assert total <= Decimal(purchase_power) + Decimal("0.000001")


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_gap_below_minimum_is_skipped(strategy):
    ideal = IdealPortfolio(holdings=[IdealPortfolioElement(ticker="AAA", weight=0.5), IdealPortfolioElement(ticker="BBB", weight=0.5)])
    # AAA is $0.50 short of target, BBB is $100 short
    real = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAA", units=1, value=Money(value="499.50")),
            RealPortfolioElement(ticker="BBB", units=1, value=Money(value=400)),
        ]
    )
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices({"AAA": Decimal(10), "BBB": Decimal(10)}),
        buy_order=strategy,
        target_size=1000,
        purchase_power=1000,
    )
    assert {o.ticker: o.value for o in plan.to_buy} == {"BBB": Money(value=100)}


def test_last_order_does_not_overshoot_remaining_cash():
    ideal = IdealPortfolio(holdings=[IdealPortfolioElement(ticker="AAA", weight=0.5), IdealPortfolioElement(ticker="BBB", weight=0.5)])
    real = RealPortfolio(holdings=[])
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices({"AAA": Decimal(10), "BBB": Decimal(10)}),
        buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
        target_size=1000,
        purchase_power=Decimal("501"),
    )
    # $1 left after the first order is under the minimum, so stop rather than buy $2
    assert [o.value for o in plan.to_buy] == [Money(value=500)]


def test_peanut_butter_spreads_cash_across_every_gap():
    ideal = IdealPortfolio(holdings=[IdealPortfolioElement(ticker=t, weight=0.25) for t in ["AAA", "BBB", "CCC", "DDD"]])
    real = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAA", units=1, value=Money(value=150)),
            RealPortfolioElement(ticker="BBB", units=1, value=Money(value=200)),
            RealPortfolioElement(ticker="CCC", units=1, value=Money(value=250)),
            # overweight, so the gaps ($100 + $50 + $0) exceed target minus holdings
            RealPortfolioElement(ticker="DDD", units=1, value=Money(value=300)),
        ]
    )
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices(dict.fromkeys(["AAA", "BBB", "CCC", "DDD"], Decimal(10))),
        buy_order=PurchaseStrategy.PEANUT_BUTTER,
        target_size=1000,
        purchase_power=75,
    )
    # half of each gap; the strategy used to be dropped after the first ticker
    values = {o.ticker: round(o.value.decimal, 6) for o in plan.to_buy}
    assert values == {"AAA": Decimal(50), "BBB": Decimal(25)}


@pytest.mark.parametrize("purchase_power", [50, 200, 1000])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_peanut_butter_spends_cash_when_small_gaps_drop_out(purchase_power, seed):
    real, ideal, prices, gaps = scenario(seed)
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices(prices),
        buy_order=PurchaseStrategy.PEANUT_BUTTER,
        target_size=10_000,
        purchase_power=purchase_power,
    )
    spent = sum((o.value.decimal for o in plan.to_buy), Decimal(0))
    # gaps under the minimum can never be bought, so they cap what can be spent
    buyable = sum((g for g in gaps.values() if g >= MIN_ORDER_MONEY.decimal), Decimal(0))
    assert spent > min(Decimal(purchase_power), buyable) * Decimal("0.99")
    # still spread: every order is the same fraction of its gap
    fractions = {round(o.value.decimal / gaps[o.ticker], 6) for o in plan.to_buy if o.value.decimal < gaps[o.ticker]}
    assert len(fractions) <= 2


def test_peanut_butter_without_room_to_buy():
    ideal = IdealPortfolio(holdings=[IdealPortfolioElement(ticker="AAA", weight=0.5), IdealPortfolioElement(ticker="BBB", weight=0.5)])
    # held value equals the target, which used to divide by zero
    real = RealPortfolio(
        holdings=[
            RealPortfolioElement(ticker="AAA", units=1, value=Money(value=400)),
            RealPortfolioElement(ticker="BBB", units=1, value=Money(value=600)),
        ]
    )
    plan = generate_order_plan(
        real,
        ideal,
        fixed_prices({"AAA": Decimal(10), "BBB": Decimal(10)}),
        buy_order=PurchaseStrategy.PEANUT_BUTTER,
        target_size=1000,
        purchase_power=500,
    )
    assert [(o.ticker, o.value) for o in plan.to_buy] == [("AAA", Money(value=100))]


@pytest.mark.parametrize(
    "value, places, expected",
    [
        (Decimal("3.14159"), 2, Decimal("3.14")),
        (Decimal("2.71828"), 3, Decimal("2.718")),
        (Decimal("0.999"), 0, Decimal(0)),
        (Decimal("5"), 2, Decimal("5")),
    ],
)
def test_round_down_to_place(value, places, expected):
    assert round_down_to_place(value, places) == expected


class RecordingProvider(BaseProvider):
    def __init__(self, price: Decimal):
        super().__init__()
        self.price = price
        self.calls: list[tuple[str, Decimal, Money | None]] = []

    def _get_instrument_price(self, ticker, at_day=None, fail_on_missing=True):
        return self.price

    def buy_instrument(self, ticker, qty, value=None):
        self.calls.append((ticker, qty, value))
        return True


@pytest.mark.parametrize(
    "price, value, units",
    [
        # $2 of a $500 share used to round up to 0.01 shares: $5
        (Decimal(500), 2, Decimal(0)),
        (Decimal(3), 10, Decimal("3.33")),
        (Decimal(7), 70, Decimal(10)),
    ],
)
def test_value_orders_round_units_down(price, value, units):
    provider = RecordingProvider(price)
    provider.handle_order_element(OrderElement(ticker="AAA", order_type=OrderType.BUY, value=Money(value=value), qty=None))
    [(_, qty, sent_value)] = provider.calls
    assert qty == units
    assert qty * price <= Decimal(value)
    assert sent_value == Money(value=value)


def bare_webull() -> tuple[WebullProvider, list]:
    provider = WebullProvider.__new__(WebullProvider)
    placed: list = []
    provider._place_order = lambda ticker, qty, side: placed.append((ticker, qty, side))  # type: ignore[method-assign]
    return provider, placed


def test_webull_skips_orders_below_one_increment():
    provider, placed = bare_webull()
    assert provider.buy_instrument("AAA", Decimal(0), Money(value=2)) is False
    assert placed == []


def test_webull_places_rounded_down_quantity():
    provider, placed = bare_webull()
    assert provider.buy_instrument("AAA", Decimal("3.33"), Money(value=10)) is True
    assert placed == [("AAA", Decimal(3), "BUY"), ("AAA", Decimal("0.33"), "BUY")]


def bare_robinhood() -> RobinhoodProvider:
    provider = RobinhoodProvider.__new__(RobinhoodProvider)
    provider._local_instrument_cache = [{"symbol": "AAA", "url": "instrument/AAA"}]  # type: ignore[attr-defined]
    provider._get_cached_value = lambda key, value=None, callable=None, **kwargs: callable()  # type: ignore[method-assign]
    return provider


@pytest.fixture
def robinhood_api(monkeypatch):
    import robin_stocks.robinhood.orders as orders
    import robin_stocks.robinhood.stocks as stocks

    posted: list[dict] = []

    class Response:
        status_code = 201

        def json(self):
            return {"id": "order-1"}

    class Session:
        def post(self, url, json=None, data=None, timeout=None):
            posted.append(json or data)
            return Response()

    monkeypatch.setattr(orders, "get_latest_price", lambda symbol, price_type, include_extended_hours: ["33.33"])
    monkeypatch.setattr(orders, "load_account_profile", lambda account_number=None, info=None: "account/1")
    monkeypatch.setattr(stocks, "SESSION", Session())
    monkeypatch.setattr(stocks, "orders_url", lambda: "orders/")
    monkeypatch.setattr(stocks, "update_session", lambda key, value: None)
    return posted


@pytest.mark.parametrize(
    "value, quantity",
    [
        # 99.98 / 33.33 is 2.9997; rounding to nearest bought 3.00 shares, $0.01 over
        (Money(value="99.98"), 2.99),
        (Money(value=100), 3.0),
        (Money(value=10), 0.3),
    ],
)
def test_robinhood_value_orders_round_quantity_down(robinhood_api, value, quantity):
    bare_robinhood()._buy_instrument("AAA", 0, value)
    [payload] = robinhood_api
    assert payload["quantity"] == quantity
    assert Decimal(str(payload["quantity"])) * Decimal("33.33") <= value.decimal


def test_robinhood_refuses_orders_below_one_increment(robinhood_api):
    with pytest.raises(OrderError):
        bare_robinhood()._buy_instrument("AAA", 0, Money(value="0.30"))
    assert robinhood_api == []


@pytest.mark.parametrize("message", ["Request was throttled. available in 0 seconds", "Too many requests for fractional orders"])
def test_robinhood_throttle_retry_keeps_value(monkeypatch, message):
    import py_portfolio_index.portfolio_providers.robinhood as robinhood

    monkeypatch.setattr(robinhood, "sleep", lambda seconds: None)
    provider = bare_robinhood()
    calls = []
    responses = [{"detail": message}, {"id": "order-1"}]

    def fake_buy(symbol, qty, value=None):
        calls.append((symbol, qty, value))
        return responses.pop(0)

    provider._buy_instrument = fake_buy  # type: ignore[method-assign]
    assert provider.buy_instrument("AAA", Decimal("0.01"), Money(value=2)) is True
    # the retry used to drop the value and buy the rounded-up quantity instead
    assert calls == [("AAA", 0.01, Money(value=2)), ("AAA", 0.01, Money(value=2))]
