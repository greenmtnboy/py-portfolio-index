from pydantic import RootModel
from py_portfolio_index.models import StockInfo
from pathlib import Path
from py_portfolio_index import PaperAlpacaProvider
from typing import List
from alpaca.common.exceptions import APIError
from sec_edgar_api import EdgarClient
import time

TICKER_INFO = r"https://www.sec.gov/files/company_tickers_exchange.json"


class StockInfoList(RootModel):
    root: List[StockInfo]


def process_ticker(
    info: StockInfo,
    provider: PaperAlpacaProvider,
    sec_api: EdgarClient,
    cik_mapping: dict[str, str],
):
    if info.cik:
        return
    try:
        provider.get_stock_info(info.ticker)
    except APIError:
        return
    cik = cik_mapping.get(info.ticker)
    if not cik:
        return
    api_response = sec_api.get_submissions(cik=cik)

    if api_response["sic"].isdigit():
        info.sic_num = int(api_response["sic"])
    info.sic_description = api_response["sicDescription"]
    info.description = api_response["description"]
    info.category = api_response["category"].replace("<br>", "")
    info.cik = int(cik_mapping[info.ticker])


def divide_chunks(lst:list, n):
    # looping till length l
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


if __name__ == "__main__":
    provider = PaperAlpacaProvider()
    import requests

    #      "User-Agent": f"{int(time.time())} {int(time.time())}@gmail.com",
    edgar = EdgarClient(user_agent=f"{int(time.time())} {int(time.time())}@gmail.com")
    mapping_raw = requests.get(
        TICKER_INFO,
        headers={"User-Agent": f"{int(time.time())} {int(time.time())}@gmail.com"},
    )
    data = mapping_raw.json()["data"]
    mapping = {}

    for row in data:
        mapping[row[2]] = row[0]

    info_cache: dict[str, StockInfo | bool] = {}
    target = (
        Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "stock_info.json"
    )
    with open(target, "r") as f:
        contents = f.read()
        existing = StockInfoList.model_validate_json(contents)
    edgar = EdgarClient(user_agent="ethan.dickinson@gmail.com")

    for chunk in divide_chunks(existing.root, 100):
        for ticker in chunk:
            process_ticker(
                ticker, provider=provider, sec_api=edgar, cik_mapping=mapping
            )

        target = (
            Path(__file__).parent.parent
            / "py_portfolio_index"
            / "bin"
            / "stock_info.json"
        )
        with open(target, "w") as f:
            f.write(existing.model_dump_json())
