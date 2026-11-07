"""Connection helpers for the official Webull OpenAPI SDK.

Webull ships one SDK, ``webull-openapi-python-sdk``, with a trade client and a
market data client that share an ``ApiClient``. Both exchange the app key for a
token on creation, and a new token waits for approval in the Webull app.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from time import sleep
from typing import Any, TypeVar

from py_portfolio_index.constants import CACHE_DIR, Logger
from py_portfolio_index.exceptions import ConfigurationError

T = TypeVar("T")

# webull rate limits aggressively and reports it as a 429; these control how
# hard we retry before giving up on a call.
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_SECONDS = 2

# how long a new token waits for approval in the Webull app
TOKEN_APPROVAL_SECONDS = 300


class WebullApi:
    """The SDK operations this library uses, off one authenticated client."""

    def __init__(self, api_client: Any):
        from webull.data.data_client import DataClient
        from webull.trade.trade_client import TradeClient

        trade = TradeClient(api_client)
        data = DataClient(api_client)
        self.account = trade.account
        self.order = trade.order
        self.order_v3 = trade.order_v3
        self.instrument = data.instrument
        self.market_data = data.market_data


def _token_dir() -> str:
    from platformdirs import user_cache_dir

    # the SDK's default is relative to the working directory, so an approved
    # token would be lost whenever the app started from somewhere else
    return str(Path(user_cache_dir(CACHE_DIR, ensure_exists=True)) / "webull")


def get_api(
    app_key: str,
    app_secret: str,
    region_id: str = "us",
    timeout: int | None = None,
    token_approval_seconds: int = TOKEN_APPROVAL_SECONDS,
) -> WebullApi:
    """Build the SDK clients for these credentials.

    With no valid token saved, this blocks until the sign-in is approved in the
    Webull app or token_approval_seconds passes.
    """
    from webull.core.client import ApiClient
    from webull.core.exception.exceptions import ClientException, ServerException

    client = ApiClient(
        app_key,
        app_secret,
        region_id,
        timeout=timeout,
        token_check_duration_seconds=token_approval_seconds,
    )
    client.set_token_dir(_token_dir())
    # setting any logger stops the SDK adding its own, which print every
    # request and write them, app key included, to a file in the working directory
    client.set_stream_logger(log_level=logging.CRITICAL, stream=StringIO())
    try:
        return WebullApi(client)
    except (ClientException, ServerException) as e:
        raise ConfigurationError(f"Could not sign in to Webull. New sign-ins must be approved in the Webull app within {token_approval_seconds} seconds: {e}")


def webull_sdk_available() -> bool:
    """Whether the official SDK is installed and importable."""
    try:
        from webull.data.data_client import DataClient  # noqa: F401
        from webull.trade.trade_client import TradeClient  # noqa: F401
    except ImportError:
        return False
    return True


def get_server_exception() -> type[Exception]:
    """The SDK exception raised for any non-2xx response."""
    from webull.core.exception.exceptions import ServerException

    return ServerException


def call_with_retries(fn: Callable[[], T]) -> T:
    """Run an SDK call, backing off and retrying while webull returns 429."""
    server_exception = get_server_exception()
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return fn()
        except server_exception as e:
            if getattr(e, "get_http_status", lambda: None)() != 429:
                raise
            if attempt == RATE_LIMIT_RETRIES - 1:
                raise
            delay = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            Logger.info(f"Webull rate limited the request; retrying in {delay}s")
            sleep(delay)
    raise RuntimeError("unreachable")
