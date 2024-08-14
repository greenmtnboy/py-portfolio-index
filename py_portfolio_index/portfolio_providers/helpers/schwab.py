import contextlib
import json
import multiprocessing
import os
import psutil
import queue
import requests
import sys
import time
import urllib
import urllib3
import warnings


from py_portfolio_index.constants import CACHE_DIR
from platformdirs import user_cache_dir
from pathlib import Path
from dataclasses import dataclass
from os import remove
from typing import TYPE_CHECKING
import atexit

if TYPE_CHECKING:
    from authlib.integrations.httpx_client import OAuth2Client

__TIME_TIME = time.time
CALLBACK_URL = "https://127.0.0.1:8182"
TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"


@dataclass
class SchwabAuthContext:
    authorization_url: str
    api_key: str
    app_secret: str
    callback_url: str
    token_path: Path
    callback_timeout: float
    output_queue: multiprocessing.Queue
    oauth: "OAuth2Client"
    server_pid: int | None


class RedirectTimeoutError(Exception):
    pass


class RedirectServerExitedError(Exception):
    pass


class TokenMetadata:
    """
    Provides the functionality required to maintain and update our view of the
    token's metadata.
    """

    def __init__(self, token, creation_timestamp, unwrapped_token_write_func):
        """
        :param token: The token to wrap in metadata
        :param creation_timestamp: Timestamp at which this token was initially
                                   created. Notably, this timestamp does not
                                   change when the token is updated.
        :unwrapped_token_write_func: Function that accepts a non-metadata
                                     wrapped token and writes it to disk or
                                     other persistent storage.
        """

        self.creation_timestamp = creation_timestamp

        # The token write function is ultimately stored in the session. When we
        # get a new token we immediately wrap it in a new sesssion. We hold on
        # to the unwrapped token writer function to allow us to inject the
        # appropriate write function.
        self.unwrapped_token_write_func = unwrapped_token_write_func

        # The current token. Updated whenever the wrapped token update function
        # is called.
        self.token = token

    @classmethod
    def from_loaded_token(cls, token, unwrapped_token_write_func):
        """
        Returns a new ``TokenMetadata`` object extracted from the metadata of
        the loaded token object. If the token has a legacy format which contains
        no metadata, assign default values.
        """
        if "creation_timestamp" not in token:
            raise ValueError(
                "WARNING: The token format has changed since this token "
                + "was created. Please delete it and create a new one."
            )

        return TokenMetadata(
            token["token"], token["creation_timestamp"], unwrapped_token_write_func
        )

    def token_age(self):
        """Returns the number of second elapsed since this token was initially
        created."""
        return int(time.time()) - self.creation_timestamp

    def wrapped_token_write_func(self):
        """
        Returns a version of the unwrapped write function which wraps the token
        in metadata and updates our view on the most recent token.
        """

        def wrapped_token_write_func(token, *args, **kwargs):
            # If the write function is going to raise an exception, let it do so
            # here before we update our reference to the current token.
            ret = self.unwrapped_token_write_func(
                self.wrap_token_in_metadata(token), *args, **kwargs
            )

            self.token = token

            return ret

        return wrapped_token_write_func

    def wrap_token_in_metadata(self, token):
        return {
            "creation_timestamp": self.creation_timestamp,
            "token": token,
        }


def __update_token(token_path):
    def update_token(t, *args, **kwargs):
        with open(token_path, "w") as f:
            json.dump(t, f)

    return update_token


# This runs in a separate process and is invisible to coverage
def __run_client_from_login_flow_server(
    q, callback_port, callback_path
):  # pragma: no cover
    """Helper server for intercepting redirects to the callback URL. See
    client_from_login_flow for details."""

    import flask

    app = flask.Flask(__name__)

    @app.route(callback_path)
    def handle_token():
        q.put(flask.request.url)
        return "schwab-py callback received! You may now close this window/tab."

    @app.route("/schwab-py-internal/status")
    def status():
        return "running"

    if callback_port == 443:
        return

    # Wrap this call in some hackery to suppress the flask startup messages
    with open(os.devnull, "w") as devnull:
        import logging

        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        old_stdout = sys.stdout
        sys.stdout = devnull
        app.run(port=callback_port, ssl_context="adhoc")
        sys.stdout = old_stdout


def __fetch_and_register_token_from_redirect(
    oauth,
    redirected_url,
    api_key,
    app_secret,
    token_path,
    asyncio,
):
    from schwab.client import AsyncClient, Client
    from schwab.debug import register_redactions
    from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuth2Client

    token = oauth.fetch_token(
        TOKEN_ENDPOINT,
        authorization_response=redirected_url,
        client_id=api_key,
        auth=(api_key, app_secret),
    )

    # Don't emit token details in debug logs
    register_redactions(token)

    # Set up token writing and perform the initial token write
    update_token = __update_token(token_path)
    metadata_manager = TokenMetadata(token, int(time.time()), update_token)
    update_token = metadata_manager.wrapped_token_write_func()
    update_token(token)

    # The synchronous and asynchronous versions of the OAuth2Client are similar
    # enough that can mostly be used interchangeably. The one currently known
    # exception is the token update function: the synchronous version expects a
    # synchronous one, the asynchronous requires an async one. The
    # oauth_client_update_token variable will contain the appropriate one.
    if asyncio:

        async def oauth_client_update_token(t, *args, **kwargs):
            update_token(t, *args, **kwargs)  # pragma: no cover

        session_class = AsyncOAuth2Client
        client_class = AsyncClient
    else:
        oauth_client_update_token = update_token
        session_class = OAuth2Client
        client_class = Client

    # Return a new session configured to refresh credentials
    return client_class(
        api_key,
        session_class(
            api_key,
            client_secret=app_secret,
            token=token,
            update_token=oauth_client_update_token,
            leeway=300,
        ),
        token_metadata=metadata_manager,
        enforce_enums=True,
    )


def create_login_context(
    api_key: str,
    app_secret: str,
    callback_url: str = CALLBACK_URL,
    callback_timeout: float = 300.0,
):
    from authlib.integrations.httpx_client import OAuth2Client
    from schwab import auth

    token_path = (
        Path(user_cache_dir(CACHE_DIR, ensure_exists=True)) / "schwab_token.json"
    )
    try:
        c = auth.client_from_token_file(token_path, api_key, app_secret=app_secret)
        c.get_account_numbers().raise_for_status()
        return
    except FileNotFoundError:
        pass
    except Exception:
        remove(token_path)
        pass
    if callback_timeout is None:
        callback_timeout = 0
    if callback_timeout < 0:
        raise ValueError("callback_timeout must be positive")

    # Start the server
    parsed = urllib.parse.urlparse(callback_url)

    # TODO: move this to validation on the call
    if parsed.hostname != "127.0.0.1":
        # TODO: document this error
        raise ValueError(
            (
                "Disallowed hostname {}. client_from_login_flow only allows "
                + "callback URLs with hostname 127.0.0.1. See here for "
                + "more information: https://schwab-py.readthedocs.io/en/"
                + "latest/auth.html#callback-url-advisory"
            ).format(parsed.hostname)
        )

    callback_port = parsed.port if parsed.port else 443
    callback_path = parsed.path if parsed.path else "/"

    output_queue: multiprocessing.Queue = multiprocessing.Queue()

    server = multiprocessing.Process(
        target=__run_client_from_login_flow_server,
        args=(output_queue, callback_port, callback_path),
    )
    server.start()

    # ensure we clean this up on exit
    def terminate_server():
        try:
            psutil.Process(server.pid).kill()
        except psutil.NoSuchProcess:
            pass

    atexit.register(terminate_server)
    # Wait until the server successfully starts
    while True:
        # Check if the server is still alive
        if server.exitcode is not None:
            # TODO: document this error
            raise RedirectServerExitedError(
                "Redirect server exited. Are you attempting to use a "
                + "callback URL without a port number specified?"
            )
        # Attempt to send a request to the server
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=urllib3.exceptions.InsecureRequestWarning
                )

                _ = requests.get(
                    "https://127.0.0.1:{}/schwab-py-internal/status".format(
                        callback_port
                    ),
                    verify=False,
                )
            break
        except requests.exceptions.ConnectionError:
            pass

        time.sleep(0.1)

    oauth = OAuth2Client(api_key, redirect_uri=callback_url)
    authorization_url, state = oauth.create_authorization_url(
        "https://api.schwabapi.com/v1/oauth/authorize"
    )

    return SchwabAuthContext(
        authorization_url=authorization_url,
        api_key=api_key,
        app_secret=app_secret,
        callback_url=callback_url,
        token_path=token_path,
        callback_timeout=callback_timeout,
        oauth=oauth,
        output_queue=output_queue,
        server_pid=server.pid,
    )


def fetch_response(context: SchwabAuthContext):
    # Context manager to kill the server upon completion
    @contextlib.contextmanager
    def callback_server():
        try:
            yield
        finally:
            try:
                psutil.Process(context.server_pid).kill()
            except psutil.NoSuchProcess:
                pass

    with callback_server():
        return _fetch_response(context)


def _fetch_response(context: SchwabAuthContext):
    # Wait for a response
    now = __TIME_TIME()
    timeout_time = now + context.callback_timeout
    received_url = None
    while True:
        now = __TIME_TIME()
        if now >= timeout_time:
            if context.callback_timeout == 0:
                # XXX: We're detecting a test environment here to avoid an
                #      infinite sleep. Surely there must be a better way to do
                #      this...
                if __TIME_TIME != time.time:  # pragma: no cover
                    raise ValueError("endless wait requested")
            else:
                break

        # Attempt to fetch from the queue
        try:
            received_url = context.output_queue.get(
                timeout=min(timeout_time - now, 0.1)
            )
            break
        except queue.Empty:
            pass

    if not received_url:
        raise RedirectTimeoutError(
            "Timed out waiting for a post-authorization callback. You "
            + "can set a longer timeout by passing a value of "
            + "callback_timeout to client_from_login_flow."
        )

    return __fetch_and_register_token_from_redirect(
        context.oauth,
        received_url,
        context.api_key,
        context.app_secret,
        context.token_path,
        False,
    )
