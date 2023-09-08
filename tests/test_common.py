from py_portfolio_index.common import round_up_to_place
import pytest
from decimal import Decimal


@pytest.mark.parametrize(
    "input, places, expected",
    [
        (Decimal("3.14159"), 2, Decimal("3.15")),
        (Decimal("2.71828"), 3, Decimal("2.719")),
        (Decimal("123.456"), 1, Decimal("123.5")),
        (Decimal("0.123"), 0, Decimal("1")),
    ],
)
def test_round_up_to_place(input, places, expected):
    result = round_up_to_place(input, places)
    assert result == expected
