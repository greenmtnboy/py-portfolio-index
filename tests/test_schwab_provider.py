from py_portfolio_index.portfolio_providers.schwab import SchwabProvider


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeStatus:
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    QUEUED = "QUEUED"
    WORKING = "WORKING"


class FakeOrder:
    Status = FakeStatus


class FakeSchwabClient:
    """Stands in for the schwab-py client, one order per status."""

    Order = FakeOrder

    def __init__(self, orders_by_status: dict[str, list[str]]):
        self.orders_by_status = orders_by_status
        self.requested: list[str] = []

    def get_orders_for_account(self, account_hash, status):
        self.requested.append(status)
        return FakeResponse([{"instrument": {"symbol": symbol}} for symbol in self.orders_by_status.get(status, [])])


def _provider_with(orders_by_status: dict[str, list[str]]) -> SchwabProvider:
    # skip __init__ so the test does not need a token file or a live login
    provider = object.__new__(SchwabProvider)
    provider._provider = FakeSchwabClient(orders_by_status)
    provider._account_hash = "account-hash"
    return provider


def test_get_unsettled_instruments_accumulates_every_status():
    provider = _provider_with(
        {
            FakeStatus.PENDING_ACTIVATION: ["AAPL"],
            FakeStatus.QUEUED: ["MSFT"],
            FakeStatus.WORKING: ["NVDA"],
        }
    )

    # each status must contribute; previously only the last one survived
    assert provider.get_unsettled_instruments() == {"AAPL", "MSFT", "NVDA"}
    assert provider._provider.requested == [
        FakeStatus.PENDING_ACTIVATION,
        FakeStatus.QUEUED,
        FakeStatus.WORKING,
    ]


def test_get_unsettled_instruments_empty_when_no_orders():
    provider = _provider_with({})
    assert provider.get_unsettled_instruments() == set()
