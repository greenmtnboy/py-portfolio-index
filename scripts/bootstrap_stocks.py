from pydantic import RootModel
from py_portfolio_index.models import StockInfo
from pathlib import Path
from py_portfolio_index import PaperAlpacaProvider
from typing import List, Generator
from requests import get
from alpaca.common.exceptions import APIError

DUMB_STOCK_API = (
    "https://dumbstockapi.com/stock?format=tickers-only&exchange=NYSE,NASDAQ,AMEX"
)


class StockInfoList(RootModel):
    root: List[StockInfo]

    @property
    def tickers(self):
        return set([x.ticker for x in self.root])


def divide_chunks(lst: list[str], n) -> Generator[List[str], None, None]:
    # looping till length l
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


if __name__ == "__main__":
    provider = PaperAlpacaProvider()

    info_cache: dict[str, StockInfo | bool] = {}

    tickers = get(DUMB_STOCK_API).json()
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
    with open(target, "w") as f:
        f.write(existing.model_dump_json())

    for chunk in divide_chunks(tickers, 100):
        for val in chunk:
            if val in existing.tickers:
                continue
            try:
                info = provider.get_stock_info(val)
            except APIError:
                continue
            if val not in existing.tickers:
                print(f"adding {val}")
                existing.root.append(info)

    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "w", encoding="utf-8") as f:
        f.write(existing.model_dump_json(indent=4))
