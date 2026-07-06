from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, List

from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest
from dotenv import load_dotenv
from pydantic import RootModel

from py_portfolio_index.models import StockInfo

if TYPE_CHECKING:
    from py_portfolio_index import PaperAlpacaProvider

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SUPPORTED_EXCHANGES = {"AMEX", "NASDAQ", "NYSE"}


class StockInfoList(RootModel):
    root: List[StockInfo]

    @property
    def tickers(self):
        return set([x.ticker for x in self.root])


def fetch_alpaca_stock_info(provider: PaperAlpacaProvider) -> list[StockInfo]:
    """Fetch the active US equity universe directly from Alpaca."""
    request = GetAssetsRequest(
        status=AssetStatus.ACTIVE,
        asset_class=AssetClass.US_EQUITY,
    )
    assets = provider.trading_client.get_all_assets(request)
    stock_info = []
    for asset in assets:
        exchange = asset.exchange.value
        if exchange not in SUPPORTED_EXCHANGES:
            continue
        stock_info.append(
            StockInfo(
                ticker=asset.symbol,
                name=asset.name,
                exchange=exchange,
                tradable=asset.tradable,
            )
        )
    return sorted(stock_info, key=lambda info: info.ticker)


def main() -> None:
    from py_portfolio_index import PaperAlpacaProvider

    provider = PaperAlpacaProvider()
    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    if not target.exists():
        existing = StockInfoList(root=[])
    else:
        with open(target, "r") as f:
            contents = f.read()
            if contents:
                existing = StockInfoList.model_validate_json(contents)

    existing_tickers = existing.tickers
    for info in fetch_alpaca_stock_info(provider):
        if info.ticker in existing_tickers:
            continue
        print(f"adding {info.ticker}")
        existing.root.append(info)
        existing_tickers.add(info.ticker)

    with open(target, "w", encoding="utf-8") as f:
        f.write(existing.model_dump_json(indent=4))


if __name__ == "__main__":
    main()
