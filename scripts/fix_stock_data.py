from pydantic import BaseModel, RootModel
from py_portfolio_index.models import StockInfo
from pathlib import Path
from py_portfolio_index import PaperAlpacaProvider
from typing import List
from alpaca.common.exceptions import APIError


class  StockInfoList(RootModel):
    root:List[StockInfo]

def validate_ticker(
    info: StockInfo,
    provider:PaperAlpacaProvider,
):  
    try:
        check = provider.get_stock_info(info.ticker)
    except APIError:
        return
    if check.name !=info.name:
        print(check.name + " != " + info.name)
        info.name = check.name
    


if __name__ == "__main__":
    provider = PaperAlpacaProvider()

    info_cache: dict[str, StockInfo | bool] = {}
    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "r") as f:
        contents = f.read()
        existing = StockInfoList.model_validate_json(contents)

    for ticker in existing.root:
        validate_ticker(
            ticker, provider=provider
        )

    final = StockInfoList(
        __root__=list([v for v in info_cache.values() if isinstance(v, StockInfo)])
    )

    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "w") as f:
        f.write(final.json())
