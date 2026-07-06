from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING

import requests
from dotenv import load_dotenv

if TYPE_CHECKING:
    from py_portfolio_index import PaperAlpacaProvider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CRSP_CURRENT_URL = (
    "https://crsp.org/wp-content/uploads/quarterly-index-constituents/"
    "crsp_quarterly_constituents.csv"
)
VANGUARD_HOLDINGS_URL = (
    "https://investor.vanguard.com/investment-products/etfs/profile/api/"
    "{symbol}/portfolio-holding/stock.json"
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class IndexHolding:
    ticker: str
    weight: str


@dataclass(frozen=True)
class IndexSnapshot:
    name: str
    as_of: date
    components: list[IndexHolding]


def _crsp_url_candidates(start: datetime) -> list[str]:
    urls = [CRSP_CURRENT_URL]
    candidate_months = [
        start - timedelta(days=days)
        for days in [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300]
    ]
    for candidate in candidate_months:
        if candidate.month not in [3, 6, 9, 12]:
            continue
        _, last_day = calendar.monthrange(candidate.year, candidate.month)
        for day in reversed(range(1, last_day + 1)):
            urls.append(
                "https://crsp.org/wp-content/uploads/"
                f"crspmi_quarterly_constituents_{candidate.year}"
                f"{candidate.month:02d}{day:02d}.csv"
            )
    return urls


def fetch_crsp_indexes(
    start: datetime | None = None,
    request_get=requests.get,
) -> list[IndexSnapshot]:
    """Fetch CRSP data and normalize each index into an IndexSnapshot."""
    response = None
    for address in _crsp_url_candidates(start or datetime.now()):
        print(f"Attempting CRSP holdings from {address}")
        try:
            candidate = request_get(
                address,
                allow_redirects=True,
                headers=REQUEST_HEADERS,
                timeout=30,
            )
        except requests.RequestException as error:
            print(f"CRSP request failed: {error}")
            continue
        if candidate.ok and candidate.text.startswith("TradeDate"):
            response = candidate
            break

    if response is None:
        raise ValueError("Could not find current CRSP index results")

    csv_reader = csv.reader(StringIO(response.text))
    next(csv_reader)
    indexes: dict[str, list[IndexHolding]] = defaultdict(list)
    as_of_dates: dict[str, date] = {}
    for row in csv_reader:
        index_name = row[2]
        ticker = row[-3].strip()
        if not ticker:
            continue
        as_of_dates[index_name] = datetime.strptime(row[0], r"%m/%d/%Y").date()
        indexes[index_name].append(IndexHolding(ticker=ticker, weight=row[-1]))

    return [
        IndexSnapshot(name=name, as_of=as_of_dates[name], components=components)
        for name, components in indexes.items()
    ]


def fetch_vanguard_index(
    symbol: str = "VTI",
    index_name: str = "Total Market",
    page_size: int = 5000,
    request_get=requests.get,
) -> IndexSnapshot:
    """Fetch Vanguard ETF holdings and normalize them into an IndexSnapshot."""
    url = VANGUARD_HOLDINGS_URL.format(symbol=symbol.upper())
    start = 1
    entities = []
    as_of = None

    while True:
        response = request_get(
            url,
            params={"start": start, "count": page_size},
            headers=REQUEST_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("fund", {}).get("entity", [])
        if not isinstance(page, list):
            raise ValueError("Vanguard response did not contain fund.entity holdings")
        entities.extend(page)
        as_of = as_of or payload.get("asOfDate")
        total_size = int(payload.get("size", len(entities)))
        if not page or len(entities) >= total_size:
            break
        start += len(page)

    if not as_of:
        raise ValueError("Vanguard response did not contain an asOfDate")

    components = []
    for entity in entities:
        ticker = str(entity.get("ticker", "")).strip()
        percent_weight = entity.get("percentWeight")
        if not ticker or percent_weight in (None, ""):
            continue
        fractional_weight = Decimal(str(percent_weight)) / Decimal(100)
        components.append(
            IndexHolding(ticker=ticker, weight=str(fractional_weight))
        )

    return IndexSnapshot(
        name=index_name,
        as_of=datetime.fromisoformat(as_of).date(),
        components=components,
    )


def validate_ticker(
    ticker: str,
    provider: PaperAlpacaProvider,
    info_cache: dict[str, bool],
    attempt: int = 0,
):
    """Use purely to see if a stock exists; do not persist any data"""
    if info_cache.get(ticker, False) is True:
        return True
    if info_cache.get(ticker, None) is False:
        return False
    try:
        info = provider.get_stock_info(ticker)
        if not info.tradable:
            info_cache[ticker] = False
            return False
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


def validate_snapshot(
    snapshot: IndexSnapshot,
    provider: PaperAlpacaProvider,
    info_cache: dict[str, bool],
) -> IndexSnapshot:
    components = []
    for holding in snapshot.components:
        if validate_ticker(holding.ticker, provider, info_cache=info_cache):
            components.append(holding)
        else:
            print(f"Failed to validate {holding.ticker}")
    return IndexSnapshot(
        name=snapshot.name,
        as_of=snapshot.as_of,
        components=components,
    )


def write_snapshot(snapshot: IndexSnapshot) -> None:
    label = snapshot.name.replace(" ", "_").replace("/", "_").lower()
    target = (
        Path(__file__).parent.parent
        / "py_portfolio_index"
        / "bin"
        / "indexes"
        / f"{label}.json"
    )
    output = {
        "name": snapshot.name,
        "as_of": snapshot.as_of.isoformat(),
        "components": [
            {"ticker": holding.ticker, "weight": holding.weight}
            for holding in snapshot.components
        ],
    }
    with open(target, "w") as file:
        json.dump(output, file, indent=2)


def update_init_file():
    init_target = Path(__file__).parent.parent / "py_portfolio_index" / "__init__.py"
    print("Updating init file")
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


def main() -> None:
    from py_portfolio_index import PaperAlpacaProvider

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["vanguard", "crsp"],
        default="crsp",
        help="Holdings source (default: CRSP)",
    )
    args = parser.parse_args()

    provider = PaperAlpacaProvider()
    info_cache: dict[str, bool] = {}
    start = datetime.now()

    if args.source == "vanguard":
        snapshots = [fetch_vanguard_index()]
    else:
        snapshots = fetch_crsp_indexes(start=start)

    for snapshot in snapshots:
        if snapshot.name.lower().startswith("crsp"):
            continue
        validated = validate_snapshot(snapshot, provider, info_cache)
        write_snapshot(validated)
        print(
            f"Wrote {len(validated.components)} {validated.name} holdings "
            f"in {datetime.now() - start}"
        )

    target = (
        Path(__file__).parent.parent
        / "py_portfolio_index"
        / "bin"
        / "cached_ticker_list.csv"
    )
    tickers = sorted(info_cache)
    with open(target, "w") as file:
        for ticker in tickers:
            file.write(f"{ticker}\n")

    update_init_file()


if __name__ == "__main__":
    main()
