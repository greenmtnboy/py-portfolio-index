from decimal import Decimal

from py_portfolio_index.portfolio_providers.alpaca_v2 import AlpacaProvider

ASSET_ID = "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415"


def make_position(symbol: str, exchange: str) -> dict:
    return {
        "asset_id": ASSET_ID,
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": "us_equity",
        "avg_entry_price": "10",
        "qty": "2",
        "side": "long",
        "market_value": "30",
        "cost_basis": "20",
        "unrealized_pl": "10",
    }


def make_asset(symbol: str, exchange: str) -> dict:
    return {
        "id": ASSET_ID,
        "class": "us_equity",
        "exchange": exchange,
        "symbol": symbol,
        "name": f"{symbol} Inc",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": True,
    }


class FakeTradingClient:
    def __init__(self, responses: dict):
        self.responses = responses

    def get(self, path, data=None):
        return self.responses[path]


def make_provider(responses: dict) -> AlpacaProvider:
    provider = AlpacaProvider(key_id="key", secret_key="secret")
    provider.trading_client = FakeTradingClient(responses)  # type: ignore[assignment]
    return provider


def test_positions_tolerate_exchanges_alpaca_py_does_not_know():
    provider = make_provider({"/positions": [make_position("AAPL", "NASDAQ"), make_position("NEWCO", "TXSE")]})

    positions = provider.get_all_positions()

    assert [p.symbol for p in positions] == ["AAPL", "NEWCO"]
    assert positions[0].exchange.value == "NASDAQ"
    assert positions[1].exchange.value == ""
    assert Decimal(positions[1].market_value) == Decimal(30)


def test_assets_tolerate_exchanges_alpaca_py_does_not_know():
    provider = make_provider({"/assets": [make_asset("AAPL", "NASDAQ"), make_asset("NEWCO", "TXSE")]})

    assert provider.valid_assets == {"AAPL", "NEWCO"}


def test_stock_info_keeps_unknown_exchange():
    provider = make_provider({"/assets/NEWCO": make_asset("NEWCO", "TXSE")})

    assert provider._get_stock_info("NEWCO") == {
        "name": "NEWCO Inc",
        "exchange": "TXSE",
        "tradable": True,
    }


def test_exchanges_alpaca_py_knows_pass_through():
    # ASCX was added to alpaca-py's enum in 0.44; once TXSE lands it will flow through the same way
    provider = make_provider({"/positions": [make_position("NEWCO", "ASCX")]})

    assert provider.get_all_positions()[0].exchange.value == "ASCX"
