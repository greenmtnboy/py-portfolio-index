import pytest

from py_portfolio_index.exceptions import ConfigurationError
from py_portfolio_index.portfolio_providers.schwab import api_helper, is_auth_error


# The exact message authlib surfaces when Schwab rejects a stale refresh token
# (the failure happens during the implicit token refresh, before the request is
# sent). This is the case the provider must translate into a re-sign-in signal.
REVOKED_REFRESH_TOKEN_ERROR = (
    'unsupported_token_type: 400 Bad Request: '
    '"{"error_description":"Refresh token is invalid, expired or revoked",'
    '"error":"invalid_grant"}"'
)


@pytest.mark.parametrize(
    "message",
    [
        REVOKED_REFRESH_TOKEN_ERROR,
        "Exception while authenticating refresh token",
        "Refresh token is invalid, expired or revoked",
        "invalid_grant",
        "unsupported_token_type",
    ],
)
def test_is_auth_error_detects_token_failures(message):
    assert is_auth_error(Exception(message)) is True


def test_is_auth_error_ignores_unrelated_errors():
    assert is_auth_error(Exception("500 Internal Server Error")) is False
    assert is_auth_error(ValueError("ticker not found")) is False


def test_api_helper_converts_refresh_failure_to_configuration_error():
    """A failed token refresh raised *during the request* must become a
    ConfigurationError so callers force a re-sign in."""

    def failing_request():
        raise Exception(REVOKED_REFRESH_TOKEN_ERROR)

    with pytest.raises(ConfigurationError):
        api_helper(failing_request)


def test_api_helper_reraises_non_auth_errors():
    def failing_request():
        raise RuntimeError("transient network blip")

    with pytest.raises(RuntimeError):
        api_helper(failing_request)


def test_api_helper_returns_json_on_success():
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    assert api_helper(lambda: DummyResponse()) == {"ok": True}
