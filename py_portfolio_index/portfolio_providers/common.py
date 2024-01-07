# Class implementing a cache
# for historic stock prices
# requires a callable interace to get values at date for a list of tickers
# accepts list of stocks + dates to get values for
# returns these from cache if possible, or for those not found
# calls provider to return prices
from collections import defaultdict
from typing import List, Dict
from datetime import date as datetype
from datetime import datetime
from decimal import Decimal


class PriceCache(object):
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.store = defaultdict(dict)
        self.instant_refresh_times: dict[str, datetime] = {}
        self.default_timeout: int = 60 * 60  # 1 hour

    def get_prices(
        self, tickers: List[str], date: datetype | None = None
    ) -> Dict[str, Decimal]:
        # if no date is provided, assume they want the instantaneous price
        if not date:
            label = "INSTANT"
        else:
            label = date.isoformat()
        cached = self.store[label]
        found = {k: v for k, v in cached.items() if k in tickers}
        if label == "INSTANT":
            for k, v in self.instant_refresh_times.items():
                if k in tickers:
                    if (datetime.now() - v).seconds > self.default_timeout:
                        found.pop(k, None)
        missing = [x for x in tickers if x not in found]
        if missing:
            prices = self.fetcher(missing, date)
            for ticker, price in prices.items():
                cached[ticker] = price
                found[ticker] = price
                if label == "INSTANT":
                    self.instant_refresh_times[ticker] = datetime.now()
        return found
