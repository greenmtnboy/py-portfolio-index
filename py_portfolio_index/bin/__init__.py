from .indexes import INDEXES
from .lists import STOCK_LISTS
from py_portfolio_index.models import StockInfo
from pathlib import Path
from json import loads

STOCK_INFO: dict[str, StockInfo] = {}
with open(Path(__file__).parent / "stock_info.json", "r") as f:
    all = loads(f.read())
    for row in all:
        STOCK_INFO[row["ticker"]] = StockInfo.parse_obj(row)

with open(Path(__file__).parent / "cached_ticker_list.csv", "r") as f:
    VALID_STOCKS = set([v for v in f.read().split("\n") if v])

__all__ = ["INDEXES", "STOCK_LISTS", "STOCK_INFO", "VALID_STOCKS"]
