from types import SimpleNamespace

from alpaca.trading.enums import AssetClass, AssetExchange, AssetStatus

from scripts.bootstrap_stocks import fetch_alpaca_stock_info


def test_fetch_alpaca_stock_info_filters_and_normalizes_assets():
    assets = [
        SimpleNamespace(
            symbol="NYSE1",
            name="NYSE Company",
            exchange=AssetExchange.NYSE,
            tradable=True,
        ),
        SimpleNamespace(
            symbol="AMEX1",
            name="AMEX Company",
            exchange=AssetExchange.AMEX,
            tradable=False,
        ),
        SimpleNamespace(
            symbol="ARCA1",
            name="ARCA Fund",
            exchange=AssetExchange.ARCA,
            tradable=True,
        ),
    ]

    class FakeTradingClient:
        def get_all_assets(self, request):
            assert request.status == AssetStatus.ACTIVE
            assert request.asset_class == AssetClass.US_EQUITY
            return assets

    provider = SimpleNamespace(trading_client=FakeTradingClient())

    results = fetch_alpaca_stock_info(provider)

    assert [result.ticker for result in results] == ["AMEX1", "NYSE1"]
    assert results[0].exchange == "AMEX"
    assert results[0].tradable is False
