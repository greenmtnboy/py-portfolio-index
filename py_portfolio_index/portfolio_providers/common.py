# Class implementing a cache
# for historic stock prices
# requires a callable interace to get values at date for a list of tickers
# accepts list of stocks + dates to get values for
# returns these from cache if possible, or for those not found
# calls provider to return prices
from collections import defaultdict
from typing import List
from datetime import date as datetype


class PriceCache(object):
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.store = defaultdict(dict)

    def get_prices(self, tickers: List[str], date: datetype | None):
        if not date:
            date = datetype.today()
        cached = self.store[date]
        found = {k: v for k, v in cached.items() if k in tickers}
        missing = [x for x in tickers if x not in cached]
        if missing:
            prices = self.fetcher(missing, date)
            for ticker, price in prices.items():
                cached[ticker] = price
                found[ticker] = price
        return found
