# Basic historical cache implementation
# Instantiation equires a callable interace to get values at date for a list of tickers
# accepts list of stocks + dates to get values for
# returns these from cache if possible, or for those not found
# calls provider to return prices
from collections import defaultdict
from typing import List, Dict
from datetime import date as datetype
from datetime import datetime
from decimal import Decimal
from py_portfolio_index.exceptions import PriceFetchError

import time
import functools
from typing import Optional, Callable, Any
import logging

# 1 hour
DEFAULT_TIMEOUT = 60 * 60


class PriceCache(object):
    def __init__(
        self, fetcher, single_fetcher=None, timeout: int = DEFAULT_TIMEOUT
    ) -> None:
        self.fetcher = fetcher
        self.single_fetcher = single_fetcher
        self.store: defaultdict[str, dict[str, Decimal | None]] = defaultdict(dict)
        self.instant_refresh_times: dict[str, datetime] = {}
        self.default_timeout: int = timeout

    @staticmethod
    def date_to_label(date: datetype | None) -> str:
        if not date:
            label = "INSTANT"
        else:
            label = date.isoformat()
        return label

    def get_price(self, ticker: str, date: datetype | None = None) -> Decimal | None:
        """If we have an optimized single stock lookup"""
        label = self.date_to_label(date)
        cached: dict[str, Decimal | None] = self.store[label]
        if ticker in cached and label == "INSTANT":
            if (
                datetime.now() - self.instant_refresh_times[ticker]
            ).seconds > self.default_timeout:
                del cached[ticker]
        if ticker in cached:
            return cached[ticker]
        try:
            price = self.single_fetcher(ticker, date)
            cached[ticker] = price
            if label == "INSTANT":
                self.instant_refresh_times[ticker] = datetime.now()
            return price
        except NotImplementedError:
            return self.get_prices([ticker], date)[ticker]
        except Exception as e:
            raise PriceFetchError([ticker], e)

    def get_prices(
        self, tickers: List[str], date: datetype | None = None
    ) -> Dict[str, Decimal | None]:
        # if no date is provided, assume they want the instantaneous price
        label = self.date_to_label(date)
        cached: dict[str, Decimal | None] = self.store[label]
        found = {k: v for k, v in cached.items() if k in tickers}
        if label == "INSTANT":
            for k, v in self.instant_refresh_times.items():
                if k in tickers:
                    if (datetime.now() - v).seconds > self.default_timeout:
                        found.pop(k, None)
        missing = [x for x in tickers if x not in found]
        if missing:
            try:
                prices: dict[str, Decimal | None] = self.fetcher(missing, date)
            except PriceFetchError:
                raise
            except Exception as e:
                raise PriceFetchError(missing, e)
            for ticker, price in prices.items():
                cached[ticker] = price
                found[ticker] = price
                if label == "INSTANT":
                    self.instant_refresh_times[ticker] = datetime.now()
        return found


def time_endpoint(
    logger: Optional[logging.Logger] = None, log_level: int = logging.INFO
) -> Callable:
    """
    Decorator to measure and log endpoint execution time.

    Args:
        logger: Optional logger instance. If None, uses print() to stdout.
        log_level: Logging level to use (default: INFO)

    Returns:
        Decorated function that logs timing information
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Get the function name (endpoint name)
            endpoint_name = func.__name__

            # Record start time
            start_time = time.time()

            try:
                # Execute the function
                result = func(*args, **kwargs)

                # Calculate execution time
                execution_time = time.time() - start_time

                # Format the message
                message = f"Endpoint '{endpoint_name}' completed in {execution_time:.4f} seconds"

                # Log or print the message
                if logger:
                    logger.log(log_level, message)
                else:
                    print(message)

                return result

            except Exception as e:
                # Calculate execution time even if there's an error
                execution_time = time.time() - start_time

                # Format error message
                error_message = f"Endpoint '{endpoint_name}' failed after {execution_time:.4f} seconds: {str(e)}"

                # Log or print the error message
                if logger:
                    logger.log(logging.ERROR, error_message)
                else:
                    print(error_message)

                # Re-raise the exception
                raise

        return wrapper

    return decorator
