"""Build eu_index.json, giving each company its home listing and its US listings.

Components come from the holdings of an iShares Europe ETF, which name each stock's
home exchange. Its symbols are bare (MC, ROP), and most of them are also US tickers
for unrelated companies: MC is LVMH in Paris but Moelis & Co in New York. So each
component's ticker is its exchange-qualified home listing in Yahoo form (MC.PA),
and its listings are the same company's US lines: NYSE/Nasdaq shares or ADRs first,
then OTC ADRs, then, only for companies with neither, OTC ordinary shares. Planning
buys the first a provider can quote.

US candidates come from OpenFIGI (US lines of the same share class, and every US
depositary receipt) and from KNOWN_US_LISTINGS. A candidate is kept only if Yahoo
gives it the home listing's issuer name and it traded in the last week, which is
what stops a lookalike ticker getting in.

    python scripts/get_eu_index.py [--fund IEUR] [--source FILE] [--cache FILE]

Each fetch of the holdings is saved to scripts/data/eu_index_source.json, which
--source reads back instead. --cache keeps Yahoo quotes between runs, as a full
build makes a few thousand requests.
"""

import argparse
import csv
import io
import json
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

EU_INDEX = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes" / "eu_index.json"
SOURCE = Path(__file__).parent / "data" / "eu_index_source.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"

# IEV tracks the S&P Europe 350; IEUR, MSCI Europe IMI, runs to a thousand names
FUNDS = {
    "IEV": "https://www.ishares.com/us/products/239736/ishares-europe-etf/latest-holdings.csv",
    "IEUR": "https://www.ishares.com/us/products/264617/ishares-core-msci-europe-etf/latest-holdings.csv",
}

# OpenFIGI exchange code -> Yahoo suffix
EXCHANGES = {
    "LN": ".L",
    "FP": ".PA",
    "NA": ".AS",
    "GY": ".DE",
    "IM": ".MI",
    "SM": ".MC",
    "BB": ".BR",
    "SS": ".ST",
    "DC": ".CO",
    "FH": ".HE",
    "NO": ".OL",
    "ID": ".IR",
    "PL": ".LS",
    "SW": ".SW",
    "AV": ".VI",
    "GA": ".AT",
}

HOME_SUFFIXES = tuple(EXCHANGES.values())

# iShares exchange names -> OpenFIGI code; "US" for the few whose home is in New York
ISHARES_EXCHANGES = {
    "London Stock Exchange": "LN",
    "Nyse Euronext - Euronext Paris": "FP",
    "Euronext Amsterdam": "NA",
    "Xetra": "GY",
    "Deutsche Boerse Xetra": "GY",
    "Borsa Italiana": "IM",
    "Bolsa De Madrid": "SM",
    "Nyse Euronext - Euronext Brussels": "BB",
    "Nasdaq Omx Nordic": "SS",
    "Omx Nordic Exchange Copenhagen A/S": "DC",
    "Nasdaq Omx Helsinki Ltd.": "FH",
    "Oslo Bors Asa": "NO",
    "Irish Stock Exchange - All Market": "ID",
    "Nyse Euronext - Euronext Lisbon": "PL",
    "SIX Swiss Exchange": "SW",
    "Wiener Boerse Ag": "AV",
    "Athens Exchange S.A. Cash Market": "GA",
    "NYSE": "US",
    "NASDAQ": "US",
}

# Where OpenFIGI's symbol differs from the exchange's
OPENFIGI_SYMBOL = {
    "KRZ": "KYGA",  # Kerry Group
    "NDA SE": "NDA",  # Nordea
    "NDA FI": "NDA",
}

# NYSE/Nasdaq listings checked against SEC registrant names, kept as candidates in
# case OpenFIGI misses them; each still has to pass the name check
KNOWN_US_LISTINGS: dict[str, list[str]] = {
    "ASML": ["ASML"],
    "SAP": ["SAP"],
    "AZN": ["AZN"],
    "NOVN": ["NVS"],
    "NOVO B": ["NVO"],
    "HSBA": ["HSBC"],
    "SHEL": ["SHEL"],
    "ULVR": ["UL"],
    "TTE": ["TTE"],
    "SAN": ["SAN", "SNY"],
    "ABI": ["BUD"],
    "UBSG": ["UBS"],
    "RIO": ["RIO"],
    "BATS": ["BTI"],
    "BBVA": ["BBVA"],
    "GSK": ["GSK"],
    "BP.": ["BP"],
    "REL": ["RELX"],
    "CRH": ["CRH"],
    "NG.": ["NGG"],
    "RACE": ["RACE"],
    "INGA": ["ING"],
    "BARC": ["BCS"],
    "LLOY": ["LYG"],
    "DBK": ["DB"],
    "EQNR": ["EQNR"],
    "NWG": ["NWG"],
    "DGE": ["DEO"],
    "ENI": ["E"],
    "ARGX": ["ARGX"],
    "FER": ["FER"],
    "FLUT": ["FLUT"],
    "HLN": ["HLN"],
    "CCEP": ["CCEP"],
    "ALC": ["ALC"],
    "ERIC B": ["ERIC"],
    "NOKIA": ["NOK"],
    "PRU": ["PUK"],
    "STLAM": ["STLA"],
    "MT": ["MT"],
    "VOD": ["VOD"],
    "PHIA": ["PHG"],
    "STMPA": ["STM"],
    "SN.": ["SNN"],
    "AER": ["AER"],
    "GMAB": ["GMAB"],
    "IHG": ["IHG"],
    "TEN": ["TS"],
    "LOGN": ["LOGI"],
    "RTO": ["RTO"],
    "QGEN": ["QGEN"],
    "GRF": ["GRFS"],
    "WPP": ["WPP"],
    "PSON": ["PSO"],
    "FME": ["FMS"],
}

# Yahoo exchange codes for NYSE, Nasdaq and the other national exchanges
US_LISTED = {"NYQ", "NMS", "NGM", "NCM", "ASE", "PCX", "BTS"}

LEGAL_FORMS = {
    "ab",
    "abp",
    "ag",
    "aktiengesellschaft",
    "as",
    "asa",
    "co",
    "corp",
    "corporation",
    "de",
    "group",
    "holding",
    "holdings",
    "inc",
    "kgaa",
    "limited",
    "ltd",
    "nv",
    "oyj",
    "plc",
    "publ",
    "sa",
    "se",
    "spa",
    "the",
}


def normalize_name(name: str) -> str:
    """An issuer name without accents, punctuation or legal form, for comparison."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    # S.A., N.V., p.l.c. and A/S lose their dots and slashes before splitting
    name = re.sub(r"(?<=\b\w)[./](?=\w\b)", "", name).replace(".", "")
    words = re.findall(r"[a-z0-9&]+", name.replace("&", " & "))
    return " ".join(w for w in words if w not in LEGAL_FORMS and w != "&")


def yahoo_symbol(local: str, exchange: str) -> str:
    # BP. is BP.L and NOVO B is NOVO-B.CO
    return local.rstrip(".").replace(" ", "-").replace(".", "-") + EXCHANGES[exchange]


def openfigi_symbol(local: str) -> str:
    # BP. is BP/ and NOVO B is NOVOB to OpenFIGI
    if local in OPENFIGI_SYMBOL:
        return OPENFIGI_SYMBOL[local]
    return local.replace(" ", "").replace(".", "/")


def openfigi(path: str, jobs: list, per_minute: int) -> list:
    """Post jobs in batches of 10, waiting out the anonymous rate limit."""
    results: list = []
    for i in range(0, len(jobs), 10):
        while True:
            response = requests.post(f"https://api.openfigi.com/v3/{path}", json=jobs[i : i + 10], timeout=60)
            if response.status_code != 429:
                break
            time.sleep(10)
        response.raise_for_status()
        results += response.json()
        time.sleep(60 / per_minute)
    return results


def openfigi_depositary_receipts() -> list[dict]:
    """Every US depositary receipt OpenFIGI knows, a few thousand."""
    out: list[dict] = []
    start = None
    while True:
        body: dict = {"exchCode": "US", "securityType2": "Depositary Receipt", "marketSecDes": "Equity"}
        if start:
            body["start"] = start
        response = requests.post("https://api.openfigi.com/v3/filter", json=body, timeout=60)
        if response.status_code == 429:
            time.sleep(15)
            continue
        response.raise_for_status()
        page = response.json()
        out += page["data"]
        start = page.get("next")
        if not start:
            return out
        time.sleep(13)


@dataclass
class Quote:
    symbol: str
    name: str
    exchange: str
    volume: float
    dollar_volume: float


QUOTES: dict[str, dict | None] = {}
CACHE: Path | None = None


def yahoo_quote(symbol: str) -> Quote | None:
    """The issuer and venue of a symbol that traded in the last week, if it did."""
    if symbol not in QUOTES:
        quote = fetch_yahoo_quote(symbol)
        QUOTES[symbol] = asdict(quote) if quote else None
        # save as we go, so an interrupted build picks up where it stopped
        if CACHE and len(QUOTES) % 100 == 0:
            CACHE.write_text(json.dumps(QUOTES), encoding="utf-8")
    found = QUOTES[symbol]
    return Quote(**found) if found else None


def fetch_yahoo_quote(symbol: str) -> Quote | None:
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "5d", "interval": "1d"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    time.sleep(0.3)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    results = response.json()["chart"]["result"]
    if not results:
        return None
    meta = results[0]["meta"]
    traded = datetime.fromtimestamp(meta.get("regularMarketTime") or 0, tz=timezone.utc)
    if datetime.now(timezone.utc) - traded > timedelta(days=7):
        return None
    name = meta.get("longName") or meta.get("shortName") or ""
    volume = meta.get("regularMarketVolume") or 0
    return Quote(symbol=symbol, name=name, exchange=meta.get("exchangeName", ""), volume=volume, dollar_volume=volume * (meta.get("regularMarketPrice") or 0))


@dataclass
class Line:
    """One share class of a company, as the fund holds it."""

    local: str
    exchange: str
    weight: float
    home: str = ""
    share_class: str | None = None
    name: str = ""


@dataclass
class Company:
    name: str
    lines: list[Line] = field(default_factory=list)

    @property
    def weight(self) -> float:
        return sum(x.weight for x in self.lines)


def resolve_home(components: list[dict]) -> list[Line]:
    """Each component's home listing, dropping the ones Yahoo hasn't seen trade."""
    lines = []
    for c in components:
        exchange = ISHARES_EXCHANGES.get(c["exchange"])
        if not exchange:
            print(f"Dropping {c['ticker']}: no known exchange ({c['exchange']})")
            continue
        lines.append(Line(local=c["ticker"], exchange=exchange, weight=float(c["weight"])))

    foreign = [x for x in lines if x.exchange != "US"]
    mapped = openfigi("mapping", [{"idType": "TICKER", "idValue": openfigi_symbol(x.local), "exchCode": x.exchange} for x in foreign], per_minute=25)
    for line, result in zip(foreign, mapped):
        line.share_class = next((row["shareClassFIGI"] for row in result.get("data", []) if row.get("shareClassFIGI")), None)

    resolved = []
    for line in lines:
        symbol = line.local if line.exchange == "US" else yahoo_symbol(line.local, line.exchange)
        quote = yahoo_quote(symbol)
        if not quote:
            print(f"Dropping {line.local}: {symbol} has not traded this week")
            continue
        line.home, line.name = symbol, quote.name
        resolved.append(line)
    return resolved


def group_companies(lines: list[Line]) -> list[Company]:
    """Merge share classes of one company, such as ATCO A and ATCO B."""
    companies: dict[str, Company] = {}
    for line in lines:
        companies.setdefault(normalize_name(line.name), Company(name=line.name)).lines.append(line)
    for company in companies.values():
        company.lines.sort(key=lambda x: -x.weight)
    return sorted(companies.values(), key=lambda x: -x.weight)


def us_listings(companies: list[Company]) -> dict[str, list[str]]:
    """Each company's US lines, verified by issuer name, keyed by its main home listing."""
    # OpenFIGI abbreviates issuer names (INDITEX-UNSPON ADR), so look every receipt
    # up on Yahoo and match on its name there
    receipt_symbols = sorted({x["ticker"] for x in openfigi_depositary_receipts()})
    receipt_set = set(receipt_symbols)
    receipts_by_name: dict[str, set[str]] = defaultdict(set)
    for symbol in receipt_symbols:
        receipt = yahoo_quote(symbol)
        if receipt:
            receipts_by_name[normalize_name(receipt.name)].add(symbol)

    classes = sorted({x.share_class for c in companies for x in c.lines if x.share_class})
    same_class = openfigi("mapping", [{"idType": "ID_BB_GLOBAL_SHARE_CLASS_LEVEL", "idValue": x, "exchCode": "US"} for x in classes], per_minute=25)
    us_lines = {k: {row["ticker"] for row in v.get("data", [])} for k, v in zip(classes, same_class)}

    out: dict[str, list[str]] = {}
    for company in companies:
        home = company.lines[0].home
        candidates: set[str] = set()
        for line in company.lines:
            candidates |= us_lines.get(line.share_class or "", set())
            candidates |= set(KNOWN_US_LISTINGS.get(line.local, []))
        target = normalize_name(company.name)
        candidates |= receipts_by_name.get(target, set())
        matched = [q for q in map(yahoo_quote, sorted(candidates - {home})) if q and normalize_name(q.name) == target]
        listed = [x for x in matched if x.exchange in US_LISTED]
        receipts_otc = [x for x in matched if x.exchange not in US_LISTED and x.symbol in receipt_set]
        ordinary_otc = [x for x in matched if x.exchange not in US_LISTED and x.symbol not in receipt_set]
        # OTC ordinary lines barely trade, so only fall back to them
        chosen = listed + receipts_otc if listed or receipts_otc else ordinary_otc
        out[home] = [x.symbol for x in sorted(chosen, key=lambda x: (x.exchange not in US_LISTED, -x.dollar_volume))]
        if not chosen and home.endswith(HOME_SUFFIXES):
            print(f"No US listing for {company.name} ({home})")
    return out


def fetch_components(fund: str) -> tuple[list[dict], str]:
    response = requests.get(FUNDS[fund], headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    text = response.content.decode("utf-8-sig")
    as_of = re.search(r'Fund Holdings as of,"([^"]+)"', text)
    if not as_of:
        raise ValueError(f"No holdings date in the {fund} file; iShares may have changed its format")
    table = text[text.index("Ticker,") :]
    rows = [r for r in csv.DictReader(io.StringIO(table)) if r.get("Asset Class") == "Equity"]
    # market value rather than the weight column, which is rounded to 0.01%
    values = [float(r["Market Value"].replace(",", "")) for r in rows]
    total = sum(values)
    components = [{"ticker": r["Ticker"], "exchange": r["Exchange"], "weight": f"{v / total:.12f}"} for r, v in zip(rows, values)]
    return components, datetime.strptime(as_of.group(1), "%b %d, %Y").date().isoformat()


def get_eu_index(fund: str, source: Path | None = None):
    if source:
        saved = json.loads(source.read_text(encoding="utf-8"))
        components, as_of, fund = saved["components"], saved["as_of"], saved["fund"]
    else:
        components, as_of = fetch_components(fund)
        SOURCE.write_text(json.dumps({"fund": fund, "as_of": as_of, "components": components}, indent=2) + "\n", encoding="utf-8")

    companies = group_companies(resolve_home(components))
    listings = us_listings(companies)
    total = sum(c.weight for c in companies)

    output = {
        "name": "EU Index",
        "source": f"iShares {fund} holdings",
        "as_of": as_of,
        "components": [
            {
                "ticker": company.lines[0].home,
                "name": company.name,
                "weight": f"{company.weight / total:.12f}",
                # other share classes, then the US lines
                "listings": [x.home for x in company.lines[1:]] + listings[company.lines[0].home],
            }
            for company in companies
        ],
    }
    with open(EU_INDEX, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    covered = sum(c.weight for c in companies if listings[c.lines[0].home] or not c.lines[0].home.endswith(HOME_SUFFIXES)) / total
    print(f"Saved eu_index.json: {len(companies)} companies, {covered:.1%} of weight with a US listing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund", choices=sorted(FUNDS), default="IEV")
    parser.add_argument("--source", type=Path, help="saved holdings to read instead of fetching")
    parser.add_argument("--cache", type=Path, help="file to keep Yahoo quotes in between runs")
    args = parser.parse_args()
    CACHE = args.cache
    if CACHE and CACHE.exists():
        QUOTES.update(json.loads(CACHE.read_text(encoding="utf-8")))
    try:
        get_eu_index(args.fund, args.source)
    finally:
        if CACHE:
            CACHE.write_text(json.dumps(QUOTES), encoding="utf-8")
