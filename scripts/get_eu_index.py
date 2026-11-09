"""Build the international indexes, giving each company its home listing and its US listings.

eu_index.json comes from an iShares Europe ETF, and world_ex_us.json from the
non-US holdings of iShares MSCI ACWI; both name each stock's home exchange.
Their symbols are bare (MC, ROP), and most of them are also US tickers
for unrelated companies: MC is LVMH in Paris but Moelis & Co in New York. So each
component's ticker is its exchange-qualified home listing in Yahoo form (MC.PA),
and its listings are the same company's US lines: NYSE/Nasdaq shares or ADRs first,
then OTC ADRs, then, only for companies with neither, OTC ordinary shares. Planning
buys the first a provider can quote.

US candidates come from OpenFIGI (US lines of the same share class, and every US
depositary receipt) and from KNOWN_US_LISTINGS. A candidate is kept only if Yahoo
gives it the home listing's issuer name and it traded in the last week, which is
what stops a lookalike ticker getting in.

    python scripts/get_eu_index.py [--index eu|world_ex_us] [--fund IEUR] [--source FILE] [--cache FILE]

Each fetch of the holdings is saved to scripts/data/<index>_source.json, which
--source reads back instead. --cache keeps Yahoo quotes and OpenFIGI answers between runs, saved as it
goes, as a full build makes a few thousand requests; a stopped build rerun
with the same --cache resumes.
"""

import argparse
import csv
import io
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

INDEX_DIR = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes"
DATA = Path(__file__).parent / "data"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"

# IEV tracks the S&P Europe 350; IEUR, MSCI Europe IMI, runs to a thousand names
FUNDS = {
    "IEV": "https://www.ishares.com/us/products/239736/ishares-europe-etf/latest-holdings.csv",
    "IEUR": "https://www.ishares.com/us/products/264617/ishares-core-msci-europe-etf/latest-holdings.csv",
    # MSCI ACWI, about 2,500 large and mid caps, 1,700 of them outside the US
    "ACWI": "https://www.ishares.com/us/products/239600/ishares-msci-acwi-etf/latest-holdings.csv",
}

# iShares exchange names -> (OpenFIGI code, Yahoo suffix); "US" for the few whose
# home is in New York. Exchanges left out (the Gulf, Moscow, Cairo) have no US
# lines worth buying, so their stocks are dropped.
ISHARES_EXCHANGES = {
    "London Stock Exchange": ("LN", ".L"),
    "Nyse Euronext - Euronext Paris": ("FP", ".PA"),
    "Euronext Amsterdam": ("NA", ".AS"),
    "Xetra": ("GY", ".DE"),
    "Deutsche Boerse Xetra": ("GY", ".DE"),
    "Borsa Italiana": ("IM", ".MI"),
    "Bolsa De Madrid": ("SM", ".MC"),
    "Nyse Euronext - Euronext Brussels": ("BB", ".BR"),
    "Nasdaq Omx Nordic": ("SS", ".ST"),
    "Omx Nordic Exchange Copenhagen A/S": ("DC", ".CO"),
    "Nasdaq Omx Helsinki Ltd.": ("FH", ".HE"),
    "Oslo Bors Asa": ("NO", ".OL"),
    "Irish Stock Exchange - All Market": ("ID", ".IR"),
    "Nyse Euronext - Euronext Lisbon": ("PL", ".LS"),
    "SIX Swiss Exchange": ("SW", ".SW"),
    "Wiener Boerse Ag": ("AV", ".VI"),
    "Athens Exchange S.A. Cash Market": ("GA", ".AT"),
    "Warsaw Stock Exchange/Equities/Main Market": ("PW", ".WA"),
    "Budapest Stock Exchange": ("HB", ".BD"),
    "Prague Stock Exchange": ("CK", ".PR"),
    "Istanbul Stock Exchange": ("TI", ".IS"),
    "Tel Aviv Stock Exchange": ("IT", ".TA"),
    "Johannesburg Stock Exchange": ("SJ", ".JO"),
    "Toronto Stock Exchange": ("CT", ".TO"),
    "Bolsa Mexicana De Valores": ("MM", ".MX"),
    "XBSP": ("BS", ".SA"),
    "Santiago Stock Exchange": ("CC", ".SN"),
    "Bolsa De Valores De Colombia": ("CB", ".CL"),
    "Tokyo Stock Exchange": ("JT", ".T"),
    "Hong Kong Exchanges And Clearing Ltd": ("HK", ".HK"),
    "Shanghai Stock Exchange": ("CG", ".SS"),
    "Shenzhen Stock Exchange": ("CS", ".SZ"),
    "Taiwan Stock Exchange": ("TT", ".TW"),
    "Gretai Securities Market": ("TT", ".TWO"),
    "Korea Exchange (Stock Market)": ("KS", ".KS"),
    "Korea Exchange (Kosdaq)": ("KQ", ".KQ"),
    "National Stock Exchange Of India": ("IS", ".NS"),
    "Bse Ltd": ("IB", ".BO"),
    "Asx - All Markets": ("AT", ".AX"),
    "New Zealand Exchange Ltd": ("NZ", ".NZ"),
    "Singapore Exchange": ("SP", ".SI"),
    "Indonesia Stock Exchange": ("IJ", ".JK"),
    "Philippine Stock Exchange Inc.": ("PM", ".PS"),
    "Stock Exchange Of Thailand": ("TB", ".BK"),
    "Bursa Malaysia": ("MK", ".KL"),
    "NYSE": ("US", ""),
    "NASDAQ": ("US", ""),
}

# sanctioned, and its stocks untradable since 2022 though index funds still carry them
EXCLUDED_COUNTRIES = {"Russia", "Russian Federation"}

HOME_SUFFIXES = tuple(sorted({suffix for _, suffix in ISHARES_EXCHANGES.values() if suffix}))

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
    # outside Europe
    "bhd",
    "berhad",
    "company",
    "cv",
    "incorporated",
    "kk",
    "pcl",
    "pt",
    "sab",
    "tbk",
}


def normalize_name(name: str) -> str:
    """An issuer name without accents, punctuation or legal form, for comparison."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    # S.A., N.V., p.l.c. and A/S lose their dots and slashes before splitting
    name = re.sub(r"(?<=\b\w)[./](?=\w\b)", "", name).replace(".", "")
    words = re.findall(r"[a-z0-9&]+", name.replace("&", " & "))
    return " ".join(w for w in words if w not in LEGAL_FORMS and w != "&")


def yahoo_symbol(local: str, suffix: str) -> str:
    if suffix == ".HK":
        # 700 is 0700.HK
        return local.zfill(4) + suffix
    if suffix in (".BK", ".IS"):
        # iShares marks Thai NVDRs (PTT.R) and Turkish lines (AKBNK.E); Yahoo doesn't
        local = re.sub(r"\.[A-Z]$", "", local)
    # Mexican series marks: WALMEX* is WALMEX.MX
    local = local.rstrip("*")
    # BP. is BP.L and NOVO B is NOVO-B.CO
    return local.rstrip(".").replace(" ", "-").replace(".", "-") + suffix


def openfigi_symbol(local: str) -> str:
    # BP. is BP/ and NOVO B is NOVOB to OpenFIGI
    if local in OPENFIGI_SYMBOL:
        return OPENFIGI_SYMBOL[local]
    return local.replace(" ", "").replace(".", "/")


def openfigi(path: str, jobs: list, per_minute: int) -> list:
    """Post jobs in batches of 10, waiting out the anonymous rate limit."""
    keys = [f"{path} {json.dumps(job, sort_keys=True)}" for job in jobs]
    todo = [(k, job) for k, job in zip(keys, jobs) if k not in FIGI]
    for i in range(0, len(todo), 10):
        batch = todo[i : i + 10]
        while True:
            response = requests.post(f"https://api.openfigi.com/v3/{path}", json=[job for _, job in batch], timeout=60)
            if response.status_code != 429:
                break
            time.sleep(10)
        response.raise_for_status()
        for (key, _), result in zip(batch, response.json()):
            FIGI[key] = result
        save_cache()
        time.sleep(60 / per_minute)
    return [FIGI[k] for k in keys]


def openfigi_depositary_receipts() -> list[dict]:
    """Every US depositary receipt OpenFIGI knows, a few thousand."""
    if "depositary receipts" in FIGI:
        return FIGI["depositary receipts"]
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
            FIGI["depositary receipts"] = out
            save_cache()
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
# US listings that trade over the counter, which some brokers don't take or charge extra for
OTC: set[str] = set()
# OpenFIGI answers, so a build that is stopped picks up where it left off
FIGI: dict = {}
CACHE: Path | None = None


def load_cache() -> None:
    if CACHE and CACHE.exists():
        saved = json.loads(CACHE.read_text(encoding="utf-8"))
        # older caches held only quotes
        QUOTES.update(saved.get("quotes", saved) if "figi" in saved else saved)
        FIGI.update(saved.get("figi", {}))


def save_cache() -> None:
    if CACHE:
        # write then rename, so a kill mid-write can't corrupt the cache
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"quotes": QUOTES, "figi": FIGI}), encoding="utf-8")
        tmp.replace(CACHE)


def yahoo_quote(symbol: str) -> Quote | None:
    """The issuer and venue of a symbol that traded in the last week, if it did."""
    if symbol not in QUOTES:
        quote = fetch_yahoo_quote(symbol)
        QUOTES[symbol] = asdict(quote) if quote else None
        # save as we go, so an interrupted build picks up where it stopped
        if len(QUOTES) % 100 == 0:
            save_cache()
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
    suffix: str = ""
    country: str = ""
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
        if c["exchange"] not in ISHARES_EXCHANGES:
            print(f"Dropping {c['ticker']}: no known exchange ({c['exchange']})")
            continue
        exchange, suffix = ISHARES_EXCHANGES[c["exchange"]]
        lines.append(Line(local=c["ticker"], exchange=exchange, suffix=suffix, country=c.get("country", ""), weight=float(c["weight"])))

    foreign = [x for x in lines if x.exchange != "US"]
    mapped = openfigi("mapping", [{"idType": "TICKER", "idValue": openfigi_symbol(x.local), "exchCode": x.exchange} for x in foreign], per_minute=25)
    for line, result in zip(foreign, mapped):
        line.share_class = next((row["shareClassFIGI"] for row in result.get("data", []) if row.get("shareClassFIGI")), None)

    resolved = []
    for line in lines:
        symbol = line.local if line.exchange == "US" else yahoo_symbol(line.local, line.suffix)
        quote = yahoo_quote(symbol)
        if not quote:
            print(f"Dropping {line.local}: {symbol} has not traded this week")
            continue
        line.home, line.name = symbol, quote.name
        resolved.append(line)
    return resolved


def group_companies(lines: list[Line]) -> list[Company]:
    """Merge share classes of one company, such as ATCO A and ATCO B.

    Lines in different countries merge only under the same full name, as A and H
    shares (601398.SS, 1398.HK) do: Siemens Limited in Mumbai is a subsidiary of
    Siemens AG, not another line of it.
    """
    companies: dict[tuple[str, str], Company] = {}
    for line in lines:
        key = (normalize_name(line.name), "")
        found = companies.get(key)
        if found and found.name != line.name and all(x.country != line.country for x in found.lines):
            key = (key[0], line.country)
        companies.setdefault(key, Company(name=line.name)).lines.append(line)
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

    shared_names = Counter(normalize_name(c.name) for c in companies)
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
        if shared_names[target] > 1:
            # Siemens AG and Siemens Limited: when two companies share a short name,
            # a US line has to carry the full one
            matched = [q for q in matched if q.name == company.name]
        listed = [x for x in matched if x.exchange in US_LISTED]
        receipts_otc = [x for x in matched if x.exchange not in US_LISTED and x.symbol in receipt_set]
        ordinary_otc = [x for x in matched if x.exchange not in US_LISTED and x.symbol not in receipt_set]
        # OTC ordinary lines barely trade, so only fall back to them
        chosen = listed + receipts_otc if listed or receipts_otc else ordinary_otc
        out[home] = [x.symbol for x in sorted(chosen, key=lambda x: (x.exchange not in US_LISTED, -x.dollar_volume))]
        OTC.update(x.symbol for x in chosen if x.exchange not in US_LISTED)
        if not chosen and home.endswith(HOME_SUFFIXES):
            print(f"No US listing for {company.name} ({home})")
    return out


def fetch_components(fund: str, ex_us: bool = False) -> tuple[list[dict], str]:
    response = requests.get(FUNDS[fund], headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    text = response.content.decode("utf-8-sig")
    as_of = re.search(r'Fund Holdings as of,"([^"]+)"', text)
    if not as_of:
        raise ValueError(f"No holdings date in the {fund} file; iShares may have changed its format")
    table = text[text.index("Ticker,") :]
    rows = [r for r in csv.DictReader(io.StringIO(table)) if r.get("Asset Class") == "Equity"]
    if ex_us:
        rows = [r for r in rows if r.get("Location") != "United States"]
    # market value rather than the weight column, which is rounded to 0.01%
    values = [float(r["Market Value"].replace(",", "")) for r in rows]
    total = sum(values)
    components = [{"ticker": r["Ticker"], "exchange": r["Exchange"], "country": r.get("Location", ""), "weight": f"{v / total:.12f}"} for r, v in zip(rows, values)]
    return components, datetime.strptime(as_of.group(1), "%b %d, %Y").date().isoformat()


@dataclass
class IndexSpec:
    name: str
    fund: str
    output: str
    # world funds hold the US too; the index is the rest
    ex_us: bool = False
    # keep companies with no US line, or drop them and spread their weight over the rest
    require_us_listing: bool = False

    @property
    def source(self) -> Path:
        return DATA / f"{Path(self.output).stem}_source.json"


INDEX_SPECS = {
    # Europe alone; the blends use world_ex_us
    "eu": IndexSpec(name="EU Index", fund="IEV", output="eu_index.json"),
    # the international sleeve of the blends: MSCI ACWI ex-US, large and mid caps
    # in 45 markets. A provider can only reach a stock through a US line, so companies
    # with none (most of mainland China, India, the Gulf) are dropped
    "world_ex_us": IndexSpec(name="World ex-US Index", fund="ACWI", output="world_ex_us.json", ex_us=True, require_us_listing=True),
}


def build_index(spec: IndexSpec, fund: str | None = None, source: Path | None = None):
    if source:
        saved = json.loads(source.read_text(encoding="utf-8"))
        components, as_of, fund = saved["components"], saved["as_of"], saved["fund"]
    else:
        fund = fund or spec.fund
        components, as_of = fetch_components(fund, spec.ex_us)
        spec.source.write_text(json.dumps({"fund": fund, "as_of": as_of, "components": components}, indent=2) + "\n", encoding="utf-8")

    excluded = [c for c in components if c.get("country") in EXCLUDED_COUNTRIES]
    if excluded:
        print(f"Excluding {len(excluded)} stocks from {', '.join(sorted(EXCLUDED_COUNTRIES))}")
    components = [c for c in components if c.get("country") not in EXCLUDED_COUNTRIES]
    companies = group_companies(resolve_home(components))
    listings = us_listings(companies)
    fund_total = sum(c.weight for c in companies)

    def reachable(company: Company) -> bool:
        home = company.lines[0].home
        return bool(listings[home]) or not home.endswith(HOME_SUFFIXES)

    if spec.require_us_listing:
        report_dropped(companies, reachable)
        companies = [c for c in companies if reachable(c)]
    total = sum(c.weight for c in companies)

    output = {
        "name": spec.name,
        "source": f"iShares {fund} holdings" + (", outside the US" if spec.ex_us else ""),
        "as_of": as_of,
        "components": [
            {
                "ticker": company.lines[0].home,
                "name": company.name,
                "weight": f"{company.weight / total:.12f}",
                # other share classes, then the US lines
                "listings": [x.home for x in company.lines[1:]] + listings[company.lines[0].home],
                **({"otc": otc} if (otc := [x for x in listings[company.lines[0].home] if x in OTC]) else {}),
            }
            for company in companies
        ],
    }
    with open(INDEX_DIR / spec.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")

    covered = sum(c.weight for c in companies if reachable(c)) / fund_total
    print(f"Saved {spec.output}: {len(companies)} companies, {covered:.1%} of the fund's weight with a US listing")


def report_dropped(companies: list[Company], reachable) -> None:
    """Print how much of each country's weight is lost for want of a US line."""
    kept: dict[str, float] = defaultdict(float)
    lost: dict[str, float] = defaultdict(float)
    for company in companies:
        for line in company.lines:
            name = line.country or "?"
            (kept if reachable(company) else lost)[name] += line.weight
    total = sum(kept.values()) + sum(lost.values())
    for name in sorted(set(kept) | set(lost), key=lambda x: -(kept[x] + lost[x])):
        share = kept[name] + lost[name]
        print(f"  {name}: {share / total:.1%} of the fund, {kept[name] / share:.0%} reachable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", choices=sorted(INDEX_SPECS), default="eu")
    parser.add_argument("--fund", choices=sorted(FUNDS), help="override the index's fund")
    parser.add_argument("--source", type=Path, help="saved holdings to read instead of fetching")
    parser.add_argument("--cache", type=Path, help="file to keep Yahoo quotes and OpenFIGI answers in between runs; rerun with it to resume")
    args = parser.parse_args()
    CACHE = args.cache
    load_cache()
    try:
        build_index(INDEX_SPECS[args.index], args.fund, args.source)
    finally:
        save_cache()
