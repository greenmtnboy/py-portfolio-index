from pathlib import Path
import csv
from io import StringIO
import requests
from datetime import datetime
from os import environ
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from finnhub import Client


def validate_ticker(ticker: str, finnhub_client: "Client", info_cache: dict[str, bool]):
    """Use purely to see if a stock exists; do not persist any data"""
    if info_cache.get(ticker, False) is True:
        return True
    try:
        lookup = finnhub_client.symbol_lookup(ticker)
        for x in lookup["result"]:
            if x["symbol"] == ticker:
                info_cache[ticker] = True
                return True
        info_cache[ticker] = False
        return False
    except Exception as e:
        print(f"Failed to validate {ticker} with error {e}")
        info_cache[ticker] = False
        return False


if __name__ == "__main__":
    import finnhub

    # Setup client
    # use this purely to validate if a ticker has had it's name changed
    # do not persist/reuse any information from this clinet
    finnhub_client = finnhub.Client(api_key=environ["FINNHUB_API_KEY"])
    info_cache: dict[str, bool] = {}

    data = requests.get("""https://www.crsp.org/files/CRSP_Constituents.csv""")
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
        if not validate_ticker(ticker, finnhub_client, info_cache=info_cache):
            print("failed to validate", ticker)
            continue
        indexes[row[2]].append(f"{ticker},{row[-1]}")
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
            / f"{label}_{dateval.year}_q{quarter}.csv"
        )
        first_row = True
        with open(target, "w") as f:
            for nrow in values:
                if first_row:
                    f.write(nrow)
                    first_row = False
                else:
                    f.write("\n")
                    f.write(nrow)

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
