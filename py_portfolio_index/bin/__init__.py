from json import loads
from pathlib import Path

from packaging import version
from pydantic import __version__

from py_portfolio_index.models import StockInfo

from .indexes import INDEXES
from .lists import STOCK_LISTS

# legacy handling
if version.parse(__version__) < version.parse("2.0.0"):
    StockInfo.model_validate = StockInfo.parse_obj  # type: ignore[method-assign,assignment]


STOCK_INFO: dict[str, StockInfo] = {}
with open(Path(__file__).parent / "stock_info.json", "r", encoding="utf-8") as f:
    content = f.read()
    if content:
        all = loads(content)
        for row in all:
            STOCK_INFO[row["ticker"]] = StockInfo.model_validate(row)

with open(Path(__file__).parent / "cached_ticker_list.csv", "r") as f:
    VALID_STOCKS = {v for v in f.read().split("\n") if v}

__all__ = ["INDEXES", "STOCK_INFO", "STOCK_LISTS", "VALID_STOCKS"]
