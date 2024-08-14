from py_portfolio_index.portfolio_providers.common import PriceCache
from random import randint

GENERATED = {}


def generate_price(ticker: str):
    if ticker in GENERATED:
        raise ValueError("Already fetched this price, should be cached")
    GENERATED[ticker] = randint(0, 1000)
    return GENERATED[ticker]


def test_price_cache():
    cache = PriceCache(
        fetcher=lambda tickers, date: {y: generate_price(y) for y in tickers},
        single_fetcher=lambda ticker, date: generate_price(ticker),
    )

    assert cache.get_price("AAPL") == cache.get_prices(["AAPL"])["AAPL"]
