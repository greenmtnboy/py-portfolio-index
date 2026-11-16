from datetime import date, datetime, timezone
from decimal import Decimal

from scripts.ingest_index_data import (
    IndexHolding,
    IndexSnapshot,
    apply_renames,
    dropped_report,
    fetch_crsp_indexes,
    fetch_vanguard_index,
    load_renames,
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
    csv_text = "TradeDate,Foo,Index,Ticker,Other,Weight\n03/31/2026,,Total Market,NVDA,,0.067\n03/31/2026,,Total Market,AAPL,,0.0629\n"

    snapshot = fetch_crsp_indexes(
        start=datetime(2026, 4, 1, tzinfo=timezone.utc),
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


def snapshot(*holdings):
    return IndexSnapshot(
        name="Total Market",
        as_of=date(2026, 11, 13),
        components=[IndexHolding(ticker=t, weight=w) for t, w in holdings],
    )


def test_renamed_ticker_keeps_its_weight_and_place():
    renamed = apply_renames(
        snapshot(("AAPL", "0.06"), ("TCBI", "0.000164"), ("ZION", "0.0001")),
        {"TCBI": "TXCP"},
    )
    assert [(h.ticker, h.weight) for h in renamed.components] == [
        ("AAPL", "0.06"),
        ("TXCP", "0.000164"),
        ("ZION", "0.0001"),
    ]


def test_source_listing_both_symbols_merges_weights():
    renamed = apply_renames(snapshot(("TCBI", "0.0001"), ("TXCP", "0.00005")), {"TCBI": "TXCP"})
    assert [(h.ticker, Decimal(h.weight)) for h in renamed.components] == [("TXCP", Decimal("0.00015"))]


def test_bundled_renames_load():
    renames = load_renames()
    assert renames["TCBI"] == "TXCP"
    assert all(old != new for old, new in renames.items())


def test_dropped_report_gives_weight_and_reason():
    report = dropped_report(
        previous={"AAPL", "ROKU", "GONE", "TCBI"},
        current={"AAPL"},
        weights={"ROKU": "0.000254"},
        failures={"ROKU": "not tradable"},
        renames={"TCBI": "TXCP"},
    )
    assert "| ROKU | 0.0254% | not tradable |" in report
    assert "| GONE | not in Total Market | no longer in the source index |" in report
    assert "Renamed tickers that still failed validation: TCBI" in report


def test_dropped_report_with_nothing_dropped():
    report = dropped_report({"AAPL"}, {"AAPL"}, {}, {}, {})
    assert "No tickers were dropped." in report
