from py_portfolio_index.enums import Currency
from py_portfolio_index.models import Money


def test_parsing():
    a = Money(value=1)
    b = Money(value=1.0)
    c = Money(value=1.0, currency=Currency.USD)
    d = Money(value=a)
    assert a == b == c == d
