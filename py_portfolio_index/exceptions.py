from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from py_portfolio_index.models import LoginResponse


class PriceFetchError(Exception):
    def __init__(self, tickers: list[str] | None = None, *args):
        super().__init__(*args)
        self.tickers = tickers or []


class ConfigurationError(Exception):
    pass


class ExtraAuthenticationStepException(Exception):
    def __init__(self, response: "LoginResponse", *args):
        super().__init__(*args)
        self.response = response


class SchwabExtraAuthenticationStepException(ExtraAuthenticationStepException):
    pass


class OrderError(Exception):
    def __init__(self, message, *args):
        super().__init__(message, *args)
        self.message = message
