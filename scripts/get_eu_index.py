import requests
from datetime import date
import json
from pathlib import Path

# The source reports home-exchange symbols, and most of them are also US tickers
# for unrelated companies: MC is LVMH in Paris but Moelis & Co in New York, ROG is
# Roche but Rogers Corp. Map each to the company's own NYSE/Nasdaq listing, checked
# against SEC registrant names; components without one are dropped, since their
# US OTC symbols can't be verified and most brokers can't trade them.
US_LISTINGS: dict[str, list[str]] = {
    "ASML": ["ASML"],
    "SAP": ["SAP"],
    "AZN": ["AZN"],
    "NOVN": ["NVS"],
    "NOVO B": ["NVO"],
    "HSBA": ["HSBC"],
    "SHEL": ["SHEL"],
    "ULVR": ["UL"],
    "TTE": ["TTE"],
    # Santander (Madrid) and Sanofi (Paris) are both SAN and the source doesn't say
    # which is which; their weights are close, so the order matters little
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
    # Prudential plc; PRU in New York is Prudential Financial
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
    # Tenaris; TEN in New York is Tsakos Energy Navigation
    "TEN": ["TS"],
    "LOGN": ["LOGI"],
    "RTO": ["RTO"],
    "QGEN": ["QGEN"],
    "SW": ["SW"],
    # Grifols; GRF in New York is Eagle Capital Growth Fund
    "GRF": ["GRFS"],
    "WPP": ["WPP"],
    "PSON": ["PSO"],
    "FME": ["FMS"],
}


EU_INDEX = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes" / "eu_index.json"


def to_us_listings(components: list[dict]) -> tuple[list[dict], float]:
    """Return the mapped components, renormalized, and the share of weight they kept."""
    seen: dict[str, int] = {}
    mapped = []
    for component in components:
        local = component["ticker"]
        listings = US_LISTINGS.get(local, [])
        occurrence = seen.get(local, 0)
        seen[local] = occurrence + 1
        if occurrence >= len(listings):
            print(f"Dropping {local}: no verified US listing")
            continue
        mapped.append({**component, "ticker": listings[occurrence]})
    total = sum(float(c["weight"]) for c in mapped)
    original = sum(float(c["weight"]) for c in components)
    return [{**c, "weight": f"{float(c['weight']) / total:.12f}"} for c in mapped], total / original


def get_eu_index():
    raw = requests.get(
        "https://www.marketscreener.com/async/graph/heatmap/components-perf?codezb=125324697&grouping=sbI&varia=F271&weight=capi",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        },
    )

    data = raw.json()["data"]

    # Get only the individual stock entries (exclude parent categories with 'id')
    stock_entries = [entry for entry in data if "mnemo" in entry]

    # Compute total value
    total_value = sum(entry["value"] for entry in stock_entries)

    # Build the components list
    components, us_listed_share = to_us_listings(
        [
            {"ticker": entry["mnemo"], "weight": f"{entry['value'] / total_value:.12f}"}
            for entry in stock_entries
        ]
    )

    # Construct the final JSON structure
    output = {
        "name": "EU Index",
        "as_of": str(date.today()),
        "us_listed_share": f"{us_listed_share:.12f}",
        "components": components,
    }

    # Save to JSON file
    with open(EU_INDEX, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("Saved eu_index.json")


if __name__ == "__main__":
    get_eu_index()
