from pathlib import Path
import csv
from io import StringIO
import requests
from datetime import datetime
import re
from time import sleep
from py_portfolio_index import PaperAlpacaProvider
import json


def validate_ticker(
    ticker: str, provider, info_cache: dict[str, bool], attempt: int = 0
):
    """Use purely to see if a stock exists; do not persist any data"""
    if info_cache.get(ticker, False) is True:
        return True
    if info_cache.get(ticker, None) is False:
        return False
    try:
        provider.get_stock_info(ticker)
        info_cache[ticker] = True
        return True

    except Exception as e:
        if "API limit reached. Please try again later. " in str(e):
            if attempt > 5:
                raise e
            sleep(30 * 1.1**attempt)
            return validate_ticker(ticker, provider, info_cache, attempt + 1)
        print(f"Failed to validate {ticker} with error {e}")
        info_cache[ticker] = False
        return False


def update_init_file():
    init_target = Path(__file__).parent.parent / "py_portfolio_index" / "__init__.py"

    with open(init_target, "r") as f:
        contents = f.read()
    from packaging import version

    find = re.search(r"__version__ = \"(?P<version>.*)\"", contents)
    version_string = find.group("version")
    parsed = version.parse(find.group("version"))
    # parsed.minor +=1
    nversion = f"{parsed.major}.{parsed.minor}.{parsed.micro+1}"
    with open(init_target, "w") as f:
        f.write(contents.replace(version_string, nversion))


if __name__ == "__main__":
    provider = PaperAlpacaProvider()
    info_cache: dict[str, bool] = {}
    today = datetime.today().date()
    found = False

    for year in (today.year, today.year - 1):
        for month in reversed(range(1, today.month)):
            smonth = str(month).zfill(2)
            address = f"""https://www.crsp.org/wp-content/uploads/{year}/{smonth}/Returns-and-Constituents-CRSP-Constituents.csv"""
            print('attempting')
            print(address)
            data = requests.get(
                address,
                allow_redirects=False,
            )
            # we got the valid csv
            if data.text.startswith("TradeDate"):
                found = True
                break
        if found:
            break
    csv_buffer = csv_buffer = StringIO(data.text)

    # Read the CSV data from the in-memory buffer using the csv.reader
    csv_reader = csv.reader(csv_buffer)
    # skip header
    next(csv_reader)
    from collections import defaultdict

    indexes: dict[str, list] = defaultdict(list)
    dateval = None
    for row in csv_reader:
        if not dateval:
            dateval = datetime.strptime(row[0], r"%m/%d/%Y").date()
        index = row[2]
        ticker = row[-3]
        if not validate_ticker(ticker, provider, info_cache=info_cache):
            print("failed to validate", ticker)
            continue
        indexes[row[2]].append({"ticker": f"{ticker}", "weight": row[-1]})
    assert dateval is not None, "dateval must be set at this point"
    quarter = (dateval.month - 1) // 3 + 1
    for key, values in indexes.items():
        if key.startswith("crsp"):
            continue
        label = key.replace(" ", "_").replace("/", "_").lower()
        target = (
            Path(__file__).parent.parent
            / "py_portfolio_index"
            / "bin"
            / "indexes"
            / f"{label}.json"
        )
        first_row = True
        final = {"name": key, "as_of": dateval.isoformat(), "components": values}
        with open(target, "w") as f:
            f.write(json.dumps(final, indent=2))

    target = (
        Path(__file__).parent.parent
        / "py_portfolio_index"
        / "bin"
        / "cached_ticker_list.csv"
    )
    list = sorted(list(info_cache.keys()))
    with open(target, "w") as f:
        for x in list:
            f.write(x)
            f.write("\n")

    update_init_file()
