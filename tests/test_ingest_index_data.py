from datetime import datetime

from scripts.ingest_index_data import (
    fetch_crsp_indexes,
    fetch_vanguard_index,
    valid_tickers,
)


class FakeResponse:
    def __init__(self, *, payload=None, text="", ok=True):
        self._payload = payload
        self.text = text
        self.ok = ok

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_fetch_vanguard_index_normalizes_and_paginates():
    responses = [
        FakeResponse(
            payload={
                "size": 2,
                "asOfDate": "2026-05-31T00:00:00-04:00",
                "fund": {
                    "entity": [
                        {"ticker": "NVDA", "percentWeight": "6.70"},
                    ]
                },
            }
        ),
        FakeResponse(
            payload={
                "size": 2,
                "asOfDate": "2026-05-31T00:00:00-04:00",
                "fund": {
                    "entity": [
                        {"ticker": "AAPL", "percentWeight": "6.29"},
                    ]
                },
            }
        ),
    ]
    calls = []

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    snapshot = fetch_vanguard_index(page_size=1, request_get=request_get)

    assert snapshot.name == "Total Market"
    assert snapshot.as_of.isoformat() == "2026-05-31"
    assert [(item.ticker, item.weight) for item in snapshot.components] == [
        ("NVDA", "0.067"),
        ("AAPL", "0.0629"),
    ]
    assert [call[1]["params"]["start"] for call in calls] == [1, 2]


def test_fetch_crsp_indexes_normalizes_csv():
    csv_text = (
        "TradeDate,Foo,Index,Ticker,Other,Weight\n"
        "03/31/2026,,Total Market,NVDA,,0.067\n"
        "03/31/2026,,Total Market,AAPL,,0.0629\n"
    )

    snapshot = fetch_crsp_indexes(
        start=datetime(2026, 4, 1),
        request_get=lambda *args, **kwargs: FakeResponse(text=csv_text),
    )[0]

    assert snapshot.name == "Total Market"
    assert snapshot.as_of.isoformat() == "2026-03-31"
    assert [(item.ticker, item.weight) for item in snapshot.components] == [
        ("NVDA", "0.067"),
        ("AAPL", "0.0629"),
    ]


def test_valid_tickers_excludes_failed_validations():
    assert valid_tickers({"NVDA": True, "INVALID": False, "AAPL": True}) == [
        "AAPL",
        "NVDA",
    ]
