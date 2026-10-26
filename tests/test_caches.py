"""Expiry and invalidation for the provider-level caches."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from py_portfolio_index.enums import ObjectKey
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider
from py_portfolio_index.portfolio_providers.common import PriceCache


class CountingFetcher:
    def __init__(self, fail: bool = False):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, tickers, date=None, fail_on_missing=True):
        self.calls.append(list(tickers))
        if self.fail:
            raise RuntimeError("quote service down")
        return {t: Decimal(10) for t in tickers}


def test_price_cache_reuses_fresh_quotes():
    fetcher = CountingFetcher()
    cache = PriceCache(fetcher=fetcher)
    cache.get_prices(["AAA", "BBB"])
    assert cache.get_prices(["AAA", "BBB", "CCC"]) == dict.fromkeys(["AAA", "BBB", "CCC"], Decimal(10))
    assert fetcher.calls == [["AAA", "BBB"], ["CCC"]]


def test_price_cache_expires_day_old_quotes():
    fetcher = CountingFetcher()
    cache = PriceCache(fetcher=fetcher)
    cache.get_prices(["AAA"])
    # timedelta.seconds of this is 10, which used to read as fresh
    cache.instant_refresh_times["AAA"] = datetime.now(timezone.utc) - timedelta(days=1, seconds=10)
    cache.get_prices(["AAA"])
    assert fetcher.calls == [["AAA"], ["AAA"]]


def test_price_cache_single_lookup_expires_day_old_quote():
    calls = []

    def single(ticker, date=None):
        calls.append(ticker)
        return Decimal(5)

    cache = PriceCache(fetcher=CountingFetcher(), single_fetcher=single)
    cache.get_price("AAA")
    cache.instant_refresh_times["AAA"] = datetime.now(timezone.utc) - timedelta(days=1, seconds=10)
    cache.get_price("AAA")
    assert calls == ["AAA", "AAA"]


def test_price_cache_tolerates_fetch_failure_when_asked():
    cache = PriceCache(fetcher=CountingFetcher(fail=True))
    # used to raise UnboundLocalError instead of returning what it had
    assert cache.get_prices(["AAA"], fail_on_missing=False) == {}


def test_price_cache_single_lookup_falls_back_to_batch():
    def single(ticker, date=None):
        raise NotImplementedError

    cache = PriceCache(fetcher=lambda tickers, date, fail_on_missing=True: {}, single_fetcher=single)
    assert cache.get_price("AAA") is None


class CachingProvider(BaseProvider):
    def __init__(self):
        super().__init__()
        self.fetches = 0

    def fetch(self):
        self.fetches += 1
        return {"fetch": self.fetches}


def test_cached_value_is_reused_until_it_expires():
    provider = CachingProvider()
    assert provider._get_cached_value(ObjectKey.ACCOUNT, callable=provider.fetch) == {"fetch": 1}
    assert provider._get_cached_value(ObjectKey.ACCOUNT, callable=provider.fetch) == {"fetch": 1}
    provider.CACHE[str(ObjectKey.ACCOUNT)].set -= timedelta(days=1, seconds=10)
    assert provider._get_cached_value(ObjectKey.ACCOUNT, callable=provider.fetch) == {"fetch": 2}
    # the refresh restarts the clock, rather than refetching on every call after
    assert provider._get_cached_value(ObjectKey.ACCOUNT, callable=provider.fetch) == {"fetch": 2}


def test_clear_cache_keeps_named_entries():
    provider = CachingProvider()
    provider._get_cached_value(ObjectKey.MISC, value="instrument_to_symbol_map", callable=provider.fetch)
    provider._get_cached_value(ObjectKey.POSITIONS, callable=provider.fetch)
    provider.clear_cache(skip_clearing=["instrument_to_symbol_map"])
    assert provider.CACHE[f"{ObjectKey.MISC}_instrument_to_symbol_map"].value == {"fetch": 1}
    assert provider.CACHE[str(ObjectKey.POSITIONS)].value is None
