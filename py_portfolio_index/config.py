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
        from py_portfolio_index.portfolio_providers.helpers.webull import (
            webull_sdk_available,
        )

        if webull_sdk_available():
            providers.append(ProviderType.WEBULL)
    except ImportError:
        pass
    try:
        from py_portfolio_index.portfolio_providers.helpers.etrade import (
            etrade_auth_available,
        )

        if etrade_auth_available():
            providers.append(ProviderType.ETRADE)
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
