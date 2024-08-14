from dataclasses import dataclass
from py_portfolio_index.enums import Currency, Provider
from typing import List


@dataclass
class Config:
    default_currency = Currency.USD


def get_providers() -> List[Provider]:
    providers = []
    try:
        from alpaca.trading.client import TradingClient  # noqa: F401

        providers.append(Provider.ALPACA)
        providers.append(Provider.ALPACA_PAPER)
    except ImportError:
        pass
    try:
        import robin_stocks.robinhood as r  # noqa: F401

        providers.append(Provider.ROBINHOOD)
    except ImportError:
        pass
    try:
        from webull import webull  # noqa: F401

        providers.append(Provider.WEBULL)
        providers.append(Provider.WEBULL_PAPER)
    except ImportError:
        pass
    try:
        from schwab import client  # noqa: F401

        providers.append(Provider.SCHWAB)
    except ImportError:
        pass
    return providers
