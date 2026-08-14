"""OAuth helpers for the E*TRADE API.

E*TRADE uses three-legged OAuth 1.0a rather than OAuth 2. A stock API key
only supports the out-of-band ("oob") flow: the user opens an authorization
URL, signs in, and copies a short verification code back to the application.
E*TRADE will register a redirect callback URL for an app on request to their
API support team; once that exists, pass a ``verifier_func`` that runs a
local callback server and extracts ``oauth_verifier`` from the redirect
instead of prompting.

The OAuth endpoints live on the production host even for sandbox consumer
keys; only the resource calls move to the sandbox host.

Access tokens expire at midnight US Eastern every day, and go inactive after
two hours without a request. Inactive tokens are revived with the renew
endpoint; expired ones require the user to authorize again.
"""

from __future__ import annotations

import json
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

from platformdirs import user_cache_dir
from pytz import UTC
from pytz import timezone as pytz_timezone

from py_portfolio_index.constants import CACHE_DIR, Logger
from py_portfolio_index.exceptions import ConfigurationError

REQUEST_TOKEN_URL = "https://api.etrade.com/oauth/request_token"
ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/access_token"
RENEW_TOKEN_URL = "https://api.etrade.com/oauth/renew_access_token"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"

TOKEN_CACHE_FILE = "etrade_token.json"
SANDBOX_TOKEN_CACHE_FILE = "etrade_sandbox_token.json"

# tokens die at midnight in this zone regardless of where the user is
_TOKEN_EXPIRY_ZONE = "US/Eastern"


def etrade_auth_available() -> bool:
    """Whether the OAuth 1.0a client library is installed."""
    try:
        import requests_oauthlib  # noqa: F401
    except ImportError:
        return False
    return True


def _oauth1_session_class() -> Any:
    """Indirection over the lazy import so tests can substitute a fake."""
    from requests_oauthlib import OAuth1Session

    return OAuth1Session


def token_path(sandbox: bool = False) -> Path:
    file = SANDBOX_TOKEN_CACHE_FILE if sandbox else TOKEN_CACHE_FILE
    return Path(user_cache_dir(CACHE_DIR, ensure_exists=True)) / file


def token_is_current(token: dict, now: Optional[datetime] = None) -> bool:
    """Whether a cached token could still be alive.

    E*TRADE access tokens expire at midnight US Eastern on the day they were
    issued, so a token from an earlier Eastern calendar day is dead no matter
    what; one from today may still need a renew call if it has gone idle.
    """
    created = token.get("created")
    if not isinstance(created, (int, float)):
        return False
    eastern = pytz_timezone(_TOKEN_EXPIRY_ZONE)
    now = now or datetime.now(tz=timezone.utc)
    created_day = datetime.fromtimestamp(created, tz=UTC).astimezone(eastern).date()
    return created_day == now.astimezone(eastern).date()


def load_cached_token(sandbox: bool = False) -> Optional[dict]:
    file = token_path(sandbox)
    if not file.exists():
        return None
    try:
        with open(file, "r") as f:
            token = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(token, dict) or not token.get("oauth_token") or not token.get("oauth_token_secret"):
        return None
    if not token_is_current(token):
        clear_cached_token(sandbox)
        return None
    return token


def save_token(token: dict, sandbox: bool = False) -> None:
    with open(token_path(sandbox), "w") as f:
        json.dump(token, f)


def clear_cached_token(sandbox: bool = False) -> None:
    file = token_path(sandbox)
    if file.exists():
        file.unlink()


def _prompt_for_verifier(authorization_url: str) -> str:
    """Default oob flow: open the authorization page and ask for the code."""
    print("Authorize this application with E*TRADE, then enter the verification code shown.")
    print(f"If a browser window does not open, visit:\n{authorization_url}")
    webbrowser.open(authorization_url)
    return input("E*TRADE verification code: ")


def _renew_session(session: Any) -> bool:
    """Revive an idle access token; False means the token is unusable."""
    try:
        response = session.get(RENEW_TOKEN_URL)
    except Exception as e:
        Logger.info(f"Could not renew E*TRADE access token: {e}")
        return False
    return response.status_code == 200


@dataclass
class ETradeAuthContext:
    """An authorization flow awaiting its verification code.

    Produced by :func:`create_login_context`; the user must visit
    ``authorization_url``, and the resulting verifier (pasted from the oob
    page, or the ``oauth_verifier`` query parameter of a registered callback
    redirect) is fed to :func:`complete_authorization`.
    """

    authorization_url: str
    api_key: str
    api_secret: str
    sandbox: bool
    # the OAuth1Session holding the request token; the verifier is only valid
    # against this exact session
    flow: Any


def _begin_authorization(api_key: str, api_secret: str, sandbox: bool, callback_url: str) -> ETradeAuthContext:
    """First leg: fetch a request token and build the authorization URL."""
    session_class = _oauth1_session_class()
    flow = session_class(api_key, client_secret=api_secret, callback_uri=callback_url)
    request_token = flow.fetch_request_token(REQUEST_TOKEN_URL)
    # E*TRADE's authorize page takes nonstandard query parameters
    query = urlencode({"key": api_key, "token": request_token["oauth_token"]})
    return ETradeAuthContext(
        authorization_url=f"{AUTHORIZE_URL}?{query}",
        api_key=api_key,
        api_secret=api_secret,
        sandbox=sandbox,
        flow=flow,
    )


def create_login_context(
    api_key: str,
    api_secret: str,
    sandbox: bool = False,
    callback_url: str = "oob",
) -> Optional[ETradeAuthContext]:
    """Begin a split authorization flow for UI-driven hosts.

    Returns None when a cached token is already usable (renewing it as a side
    effect), meaning the caller can construct a provider immediately.
    Otherwise returns a context whose ``authorization_url`` the user must
    visit; finish with :func:`complete_authorization`.

    ``callback_url`` stays "oob" unless E*TRADE's API support team has
    registered a redirect URL for this consumer key, in which case pass that
    URL and read ``oauth_verifier`` off the redirect instead of prompting.
    """
    session_class = _oauth1_session_class()
    token = load_cached_token(sandbox)
    if token:
        session = session_class(
            api_key,
            client_secret=api_secret,
            resource_owner_key=token["oauth_token"],
            resource_owner_secret=token["oauth_token_secret"],
        )
        if _renew_session(session):
            return None
        Logger.info("Cached E*TRADE access token could not be renewed; re-authorizing.")
        clear_cached_token(sandbox)
    return _begin_authorization(api_key, api_secret, sandbox, callback_url)


def complete_authorization(context: ETradeAuthContext, verifier: str) -> None:
    """Final leg: trade the verifier for an access token and cache it.

    After this succeeds, ``ETradeProvider(external_auth=True)`` (or another
    :func:`get_authenticated_session` call) will pick up the cached token.
    """
    verifier = (verifier or "").strip()
    if not verifier:
        raise ConfigurationError("No E*TRADE verification code provided; cannot complete authorization.")
    token = context.flow.fetch_access_token(ACCESS_TOKEN_URL, verifier=verifier)
    save_token(
        {
            "oauth_token": token["oauth_token"],
            "oauth_token_secret": token["oauth_token_secret"],
            "created": time.time(),
        },
        context.sandbox,
    )


def _login(
    api_key: str,
    api_secret: str,
    sandbox: bool,
    verifier_func: Callable[[str], str],
) -> Any:
    """Run the three-legged authorization and return an authenticated session."""
    context = _begin_authorization(api_key, api_secret, sandbox, "oob")
    complete_authorization(context, verifier_func(context.authorization_url))
    token = load_cached_token(sandbox)
    if not token:  # pragma: no cover - complete_authorization just saved it
        raise ConfigurationError("E*TRADE authorization did not produce a usable token.")
    session_class = _oauth1_session_class()
    return session_class(
        api_key,
        client_secret=api_secret,
        resource_owner_key=token["oauth_token"],
        resource_owner_secret=token["oauth_token_secret"],
    )


def get_authenticated_session(
    api_key: str,
    api_secret: str,
    sandbox: bool = False,
    interactive: bool = True,
    verifier_func: Optional[Callable[[str], str]] = None,
) -> Any:
    """Return an OAuth1Session holding a live E*TRADE access token.

    Reuses (and renews) a cached token from a previous run when possible;
    otherwise runs the interactive authorization flow, unless ``interactive``
    is False, in which case a dead cache is a hard error.
    """
    session_class = _oauth1_session_class()
    token = load_cached_token(sandbox)
    if token:
        session = session_class(
            api_key,
            client_secret=api_secret,
            resource_owner_key=token["oauth_token"],
            resource_owner_secret=token["oauth_token_secret"],
        )
        if _renew_session(session):
            return session
        Logger.info("Cached E*TRADE access token could not be renewed; re-authorizing.")
        clear_cached_token(sandbox)
    if not interactive:
        raise ConfigurationError("No valid cached E*TRADE token and interactive authorization is disabled. Re-run an interactive session to authorize.")
    return _login(api_key, api_secret, sandbox, verifier_func or _prompt_for_verifier)
