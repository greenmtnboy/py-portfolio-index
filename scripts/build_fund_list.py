"""Build lists/funds.csv: tickers of mutual funds, ETFs, ETPs and closed-end funds.

Pass it to IdealPortfolio.exclude to keep an index to individual stocks. Index
feeds can carry funds directly, or through foreign tickers that collide with a
US fund (DiaSorin trades as DIA in Milan, which a US broker fills as the
SPDR Dow Jones ETF).
"""

import re
import time
from pathlib import Path

import requests

FUND_TICKERS = r"https://www.sec.gov/files/company_tickers_mf.json"
LISTED_TICKERS = r"https://www.sec.gov/files/company_tickers_exchange.json"
TARGET = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "lists" / "funds.csv"

# exchange-listed products that register outside the mutual fund registry:
# UITs (SPY, DIA), grantor trusts (GLD, IBIT), and closed-end funds
FUND_NAME = re.compile(
    r"\b(ETF|ETFS|ETN|ETNS|FUND|FUNDS|ISHARES|SPDR|PROSHARES|DIREXION|GRAYSCALE|CURRENCYSHARES|MUNICIPAL|MUNI)\b"
    r"|\b(GOLD|SILVER|PLATINUM|PALLADIUM|PRECIOUS METALS|BITCOIN|ETHER|ETHEREUM|SOLANA|XRP|CRYPTO|OIL|NATURAL GAS|COMMODITY) TRUST\b",
    re.IGNORECASE,
)
# closed-end funds that are just called "<sponsor> ... Trust"
CEF_SPONSOR = re.compile(
    r"^(BLACKROCK|NUVEEN|EATON VANCE|PIMCO|GABELLI|COHEN & STEERS|CALAMOS|WESTERN ASSET|ABRDN|ABERDEEN|VIRTUS|"
    r"JOHN HANCOCK|MFS|NEUBERGER|ALLSPRING|TEMPLETON|DWS|PUTNAM|PIONEER|LIBERTY ALL[- ]STAR|ROYCE|TEKLA|"
    r"DOUBLELINE|FLAHERTY|KAYNE ANDERSON|TORTOISE|CLEARBRIDGE|DUFF & PHELPS|SABA|FIRST TRUST|PGIM|"
    r"MORGAN STANLEY|INVESCO|GUGGENHEIM|NEW GERMANY|MEXICO|TAIWAN|KOREA|CHINA|INDIA)\b.*"
    r"\b(TRUST|FUND|INCOME|MUNI\w*|PORTFOLIO|OPPORTUNIT\w*|STRATEG\w*)\b",
    re.IGNORECASE,
)
# sponsors also lend their names to REITs and banks (Franklin BSP Realty Trust)
OPERATING_NAME = re.compile(r"\b(REALTY|PROPERTIES|PROPERTY|REIT|BANCORP|BANK|BANCSHARES)\b", re.IGNORECASE)


def fetch(url: str) -> list[list]:
    # SEC rejects requests without a contact-style user agent
    response = requests.get(url, headers={"User-Agent": f"{int(time.time())} {int(time.time())}@gmail.com"}, timeout=60)
    response.raise_for_status()
    return response.json()["data"]


def build_fund_list() -> list[str]:
    # fields: cik, name, ticker, exchange
    listed = {row[2].upper(): row for row in fetch(LISTED_TICKERS) if row[2]}
    funds: set[str] = set()
    # fields: cik, seriesId, classId, symbol
    for cik, _, _, symbol in fetch(FUND_TICKERS):
        symbol = symbol.upper()
        current = listed.get(symbol)
        # the fund registry keeps symbols an operating company has since reused
        if current and current[0] != cik and not FUND_NAME.search(current[1]):
            continue
        funds.add(symbol)
    for symbol, (_, name, _, _) in listed.items():
        if FUND_NAME.search(name) or (CEF_SPONSOR.search(name) and not OPERATING_NAME.search(name)):
            funds.add(symbol)
    return sorted(funds)


if __name__ == "__main__":
    funds = build_fund_list()
    with open(TARGET, "w") as f:
        f.write("\n".join(funds))
    print(f"Wrote {len(funds)} fund tickers to {TARGET}")
