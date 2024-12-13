from sys import path
from os.path import dirname

nb_path = __file__
root_path = dirname(dirname(__file__))

print(root_path)
path.insert(0, root_path)


from pydantic import RootModel
from py_portfolio_index.models import StockInfo
from pathlib import Path
from typing import List
import financedatabase as fd

# Initialize the Equities database
import numpy as np


class StockInfoList(RootModel):
    root: List[StockInfo]


def divide_chunks(lst: list, n):
    # looping till length l
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


if __name__ == "__main__":
    equities = fd.Equities()

    # Obtain all data available excluding international exchanges
    final = {}
    df = equities.search().replace({np.nan: None})
    df = df[df.index.notnull()]
    for x in df.itertuples():
        if x.Index == np.nan:
            continue
        final[x.Index] = StockInfo(
            ticker=x.Index,
            name=x.name,
            country=x.country,
            sector=x.sector,
            industry=x.industry_group,
            market_cap=x.market_cap,
            description=x.summary,
            location=x.city,
            exchange=x.exchange,
        )

    enriched = final
    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "r", encoding="utf-8") as f:
        contents = f.read()
        existing = StockInfoList.model_validate_json(contents)
    for chunk in divide_chunks(existing.root, 100):
        for ticker_info in chunk:
            updated: StockInfo = enriched.get(ticker_info.ticker)
            if not updated:
                continue
            ticker_info.country = updated.country
            ticker_info.description = updated.description
            ticker_info.sector = updated.sector
            ticker_info.industry = updated.industry
            ticker_info.market_cap = updated.market_cap
            ticker_info.location = updated.location

    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "w", encoding="utf-8") as f:
        f.write(existing.model_dump_json(indent=4))
