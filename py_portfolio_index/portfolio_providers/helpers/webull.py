"""Connection helpers for the official Webull OpenAPI SDK.

The SDK is published as a family of packages (``webull-python-sdk-core``,
``webull-python-sdk-trade``, ``webull-python-sdk-mdata``) rather than a single
distribution, and ``webullsdktrade.api.API`` is the aggregate entry point that
exposes account, order, instrument and market-data operations off one client.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from time import sleep
from types import ModuleType
from typing import Any, Callable, TypeVar

from py_portfolio_index.constants import Logger

T = TypeVar("T")

# webull rate limits aggressively and reports it as a 429; these control how
# hard we retry before giving up on a call.
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_SECONDS = 2

_SHIMS_INSTALLED = False


def _install_vendored_shims() -> None:
    """Point the SDK's vendored ``requests``/``six`` at the real libraries.

    ``webull-python-sdk-core`` vendors requests 2.x and six 1.11, neither of
    which imports on modern Python: the vendored ``requests.utils`` imports
    ``cgi`` (removed in 3.13), and vendored six installs a ``sys.meta_path``
    finder implementing only the ``find_module`` hook (removed in 3.12).

    The SDK touches just a handful of ordinary ``requests`` entry points and
    reads only ``six.PY2`` plus ``six.moves.urllib.parse``, so aliasing those
    vendored module names to the maintained stdlib/``requests`` equivalents is
    enough to make the SDK import and run.
    """
    global _SHIMS_INSTALLED
    if _SHIMS_INSTALLED:
        return

    import importlib
    import urllib.parse

    import requests
    import requests.adapters
    import requests.status_codes
    import requests.structures

    # the parent package has to exist before we register children under it
    import webullsdkcore.vendored  # noqa: F401

    # webullsdkcore.client imports OrderedDict from requests.structures, which
    # modern requests no longer re-exports. Shadow the module with a copy that
    # carries it rather than mutating the real requests package.
    structures = ModuleType("webullsdkcore.vendored.requests.structures")
    structures.__dict__.update(requests.structures.__dict__)
    structures.OrderedDict = OrderedDict  # type: ignore[attr-defined]

    # The six module itself imports cleanly; it is only the lazy `six.moves`
    # submodules, resolved through that dead meta_path hook, that fail. Import
    # it normally so its other exports (iterkeys, PY2, ...) stay intact and
    # register just the `moves` names the SDK reaches for.
    import webullsdkcore.vendored.six as six

    six_moves = ModuleType("webullsdkcore.vendored.six.moves")
    six_moves_urllib = ModuleType("webullsdkcore.vendored.six.moves.urllib")
    six.moves = six_moves  # type: ignore[attr-defined]
    six_moves.urllib = six_moves_urllib  # type: ignore[attr-defined]
    six_moves_urllib.parse = urllib.parse  # type: ignore[attr-defined]

    replacements: dict[str, ModuleType] = {
        "webullsdkcore.vendored.requests": requests,
        "webullsdkcore.vendored.requests.__version__": importlib.import_module("requests.__version__"),
        "webullsdkcore.vendored.requests.adapters": requests.adapters,
        "webullsdkcore.vendored.requests.status_codes": requests.status_codes,
        "webullsdkcore.vendored.requests.structures": structures,
        "webullsdkcore.vendored.six.moves": six_moves,
        "webullsdkcore.vendored.six.moves.urllib": six_moves_urllib,
        "webullsdkcore.vendored.six.moves.urllib.parse": urllib.parse,
    }
    for name, module in replacements.items():
        # don't displace anything a caller already imported successfully
        sys.modules.setdefault(name, module)

    _SHIMS_INSTALLED = True


def get_api(
    app_key: str,
    app_secret: str,
    region_id: str = "us",
    timeout: int | None = None,
) -> Any:
    """Build the aggregate ``webullsdktrade`` API object for these credentials."""
    _install_vendored_shims()

    from webullsdkcore.client import ApiClient
    from webullsdktrade.api import API

    return API(ApiClient(app_key, app_secret, region_id, timeout=timeout))


def webull_sdk_available() -> bool:
    """Whether the official SDK is installed and importable.

    The SDK cannot be imported without the shims above, so this has to go
    through the same path a real client would.
    """
    try:
        _install_vendored_shims()
        import webullsdktrade.api  # noqa: F401
    except ImportError:
        return False
    return True


def get_server_exception() -> type[Exception]:
    """The SDK exception raised for any non-2xx response."""
    _install_vendored_shims()

    from webullsdkcore.exception.exceptions import ServerException

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
