# Basic historical cache implementation
# Instantiation equires a callable interace to get values at date for a list of tickers
# accepts list of stocks + dates to get values for
# returns these from cache if possible, or for those not found
# calls provider to return prices
import functools
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date as datetype
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from py_portfolio_index.exceptions import PriceFetchError

# 1 hour
DEFAULT_TIMEOUT = 60 * 60


class PriceCache:
    def __init__(self, fetcher, single_fetcher=None, timeout: int = DEFAULT_TIMEOUT) -> None:
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
        if ticker in cached and label == "INSTANT" and (datetime.now(timezone.utc) - self.instant_refresh_times[ticker]).seconds > self.default_timeout:
            del cached[ticker]
        if ticker in cached:
            return cached[ticker]
        try:
            price = self.single_fetcher(ticker, date)
            cached[ticker] = price
            if label == "INSTANT":
                self.instant_refresh_times[ticker] = datetime.now(timezone.utc)
            return price
        except NotImplementedError:
            return self.get_prices([ticker], date)[ticker]
        except Exception as e:
            raise PriceFetchError([ticker], e)

    def get_prices(
        self,
        tickers: list[str],
        date: datetype | None = None,
        fail_on_missing: bool = True,
    ) -> dict[str, Decimal | None]:
        # if no date is provided, assume they want the instantaneous price
        label = self.date_to_label(date)
        cached: dict[str, Decimal | None] = self.store[label]
        found = {k: v for k, v in cached.items() if k in tickers}
        if label == "INSTANT":
            for k, v in self.instant_refresh_times.items():
                if k in tickers and (datetime.now(timezone.utc) - v).seconds > self.default_timeout:
                    found.pop(k, None)
        missing = [x for x in tickers if x not in found]
        if missing:
            try:
                prices: dict[str, Decimal | None] = self.fetcher(missing, date, fail_on_missing=fail_on_missing)
            except PriceFetchError:
                if fail_on_missing:
                    raise
            except Exception as e:
                if fail_on_missing:
                    raise PriceFetchError(missing, e)
            for ticker, price in prices.items():
                cached[ticker] = price
                found[ticker] = price
                if label == "INSTANT":
                    self.instant_refresh_times[ticker] = datetime.now(timezone.utc)
        return found


def instance_cache(func: Callable) -> Callable:
    """Memoize a method on the instance rather than the class.

    functools.cache keys on `self`, so the class-level cache keeps every
    provider it has ever seen alive. This stores results on the instance
    instead, so they are collected along with the provider.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs) -> Any:
        cache: dict[Any, Any] = self.__dict__.setdefault("_instance_cache", {}).setdefault(func.__name__, {})
        key = (args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = func(self, *args, **kwargs)
        return cache[key]

    return wrapper


def time_endpoint(logger: logging.Logger | None = None, log_level: int = logging.INFO) -> Callable:
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
                error_message = f"Endpoint '{endpoint_name}' failed after {execution_time:.4f} seconds: {e!s}"

                # Log or print the error message
                if logger:
                    logger.log(logging.ERROR, error_message)
                else:
                    print(error_message)

                # Re-raise the exception
                raise

        return wrapper

    return decorator
