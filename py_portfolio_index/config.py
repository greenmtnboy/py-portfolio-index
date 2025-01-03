from dataclasses import dataclass
from py_portfolio_index.enums import Currency, ProviderType
from typing import List


@dataclass
class Config:
    default_currency = Currency.USD


def get_providers() -> List[ProviderType]:
    providers = []
    try:
        from alpaca.trading.client import TradingClient  # noqa: F401

        providers.append(ProviderType.ALPACA)
        providers.append(ProviderType.ALPACA_PAPER)
    except ImportError:
        pass
    try:
        import robin_stocks.robinhood as r  # noqa: F401

        providers.append(ProviderType.ROBINHOOD)
    except ImportError:
        pass
    try:
        from webull import webull  # noqa: F401

        providers.append(ProviderType.WEBULL)
        providers.append(ProviderType.WEBULL_PAPER)
    except ImportError:
        pass
    try:
        from schwab import client  # noqa: F401

        providers.append(ProviderType.SCHWAB)
    except ImportError:
        pass
    try:
        from moomoo import OpenSecTradeContext  # noqa: F401

        providers.append(ProviderType.MOOMOO)
    except ImportError:
        pass
    return providers
