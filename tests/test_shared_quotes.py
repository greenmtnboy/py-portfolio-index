from datetime import date
from decimal import Decimal

from py_portfolio_index.enums import ProviderType, PurchaseStrategy
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.models import CompositePortfolio, IdealPortfolio, IdealPortfolioElement, Money
from py_portfolio_index.operators import generate_composite_order_plan
from py_portfolio_index.portfolio_providers.common import PriceCache
from py_portfolio_index.portfolio_providers.local_dict import LocalDictProvider

PRICES = {"AAPL": Decimal(50), "MSFT": Decimal(50), "TCEHY": Decimal(50)}


class Counting(LocalDictProvider):
    """Quotes from a fixed table, recording each symbol it is asked for."""

    def __init__(self, prices=PRICES, fail: Exception | None = None):
        super().__init__(holdings=[], price_dict=dict(prices), cash=Money(value=1000))
        self.asked: list[str] = []
        self.fail = fail

    def _get_instrument_prices(self, tickers: list[str], at_day: date | None = None, fail_on_missing: bool = True):
        self.asked += tickers
        if self.fail:
            raise self.fail
        return {x: self._price_dict.get(x) for x in tickers}


class Batch(Counting):
    PROVIDER = ProviderType.SCHWAB
    QUOTE_PRIORITY = 4


class OneAtATime(Counting):
    PROVIDER = ProviderType.WEBULL
    SUPPORTS_OTC = False


class OtcBuyer(Counting):
    PROVIDER = ProviderType.MOOMOO


def ideal() -> IdealPortfolio:
    return IdealPortfolio(
        holdings=[
            IdealPortfolioElement(ticker="AAPL", weight=Decimal("0.4")),
            IdealPortfolioElement(ticker="MSFT", weight=Decimal("0.4")),
            # Tencent: no US line but an OTC ADR, and a home symbol no US broker quotes
            IdealPortfolioElement(ticker="0700.HK", weight=Decimal("0.2"), listings=["TCEHY"], otc=["TCEHY"]),
        ]
    )


def plan(*providers):
    return generate_composite_order_plan(
        CompositePortfolio([x.get_holdings() for x in providers]),
        ideal(),
        purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
        target_size=2000,
        safety_threshold=Decimal(1),
    )


def bought(result, provider) -> set[str]:
    return {x.ticker for x in result[provider.PROVIDER].to_buy}


def test_slow_provider_is_priced_by_the_batch_quoter():
    slow, fast = OneAtATime(), Batch()
    result = plan(slow, fast)

    assert slow.asked == []
    # each symbol once, however many providers plan against it
    assert sorted(fast.asked) == sorted(set(fast.asked))
    assert bought(result, slow) | bought(result, fast) >= {"AAPL", "MSFT"}


def test_otc_name_the_quoter_cannot_price_is_asked_of_its_buyer():
    fast = Batch(prices={"AAPL": Decimal(50), "MSFT": Decimal(50)})
    otc = OtcBuyer()
    result = plan(fast, otc)

    assert "TCEHY" in otc.asked
    assert "TCEHY" in bought(result, otc)
    # listed names are left to the quoter
    assert not {"AAPL", "MSFT"} & set(otc.asked)


def test_broken_quoter_hands_pricing_back_to_each_provider():
    broken = Batch(fail=ConnectionError("logged out"))
    slow = OneAtATime()
    result = plan(broken, slow)

    assert {"AAPL", "MSFT"} <= set(slow.asked)
    assert {"AAPL", "MSFT"} <= bought(result, slow)


def test_without_a_batch_quoter_each_provider_prices_itself():
    slow, otc = OneAtATime(), OtcBuyer()
    plan(slow, otc)

    assert slow.asked and otc.asked


def test_price_cache_keeps_quotes_from_a_partly_failed_fetch():
    calls: list[list[str]] = []

    def fetcher(tickers, date=None, fail_on_missing=True):
        calls.append(list(tickers))
        found = {x: Decimal(1) for x in tickers if x != "BAD"}
        raise PriceFetchError(["BAD"], "no BAD", prices=found)

    cache = PriceCache(fetcher=fetcher)
    try:
        cache.get_prices(["AAPL", "BAD"])
    except PriceFetchError:
        pass
    assert cache.get_prices(["AAPL"]) == {"AAPL": Decimal(1)}
    assert calls == [["AAPL", "BAD"]]
