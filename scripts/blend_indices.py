import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes"

# output name -> (display name, US weight, international index)
BLENDS = {
    "blended_world_60_40": ("Blended US+World ex-US 60/40", 0.6, "world_ex_us"),
    "blended_world_70_30": ("Blended US+World ex-US 70/30", 0.7, "world_ex_us"),
    "blended_world_80_20": ("Blended US+World ex-US 80/20", 0.8, "world_ex_us"),
}


def blend(output: str, name: str, us_ratio: float, intl_index: str, us_index: str = "total_market"):
    intl_file = BASE / f"{intl_index}.json"
    if not intl_file.exists():
        print(f"Skipping {output}: no {intl_file.name}")
        return
    with open(BASE / f"{us_index}.json", "r", encoding="utf-8") as f:
        us_data = json.load(f)
    with open(intl_file, "r", encoding="utf-8") as f:
        intl_data = json.load(f)

    # every international component carries its home listing, so the sleeve is its
    # full share; a provider that can trade none of a company's listings leaves its
    # share in cash rather than spreading it over the companies it can buy
    combined: dict[str, dict] = {}
    # any listing of a company finds it, so one also in the US index (CRH) is merged
    owner: dict[str, str] = {}
    for components, ratio in ((us_data["components"], us_ratio), (intl_data["components"], 1 - us_ratio)):
        # scale each side to its own total, so the split is exact even when a
        # source has lost a few delisted tickers
        side_total = sum(float(comp["weight"]) for comp in components)
        for comp in components:
            symbols = [comp["ticker"], *comp.get("listings", [])]
            key = next((owner[x] for x in symbols if x in owner), comp["ticker"])
            entry = combined.setdefault(key, {"weight": 0.0, "listings": [], "otc": []})
            entry["weight"] += float(comp["weight"]) / side_total * ratio
            for symbol in symbols:
                if symbol != key and symbol not in entry["listings"]:
                    entry["listings"].append(symbol)
                owner.setdefault(symbol, key)
            entry["otc"] += [x for x in comp.get("otc", []) if x not in entry["otc"]]

    # Normalize so total weight = 1.0
    total_weight = sum(x["weight"] for x in combined.values())

    normalized_components = sorted(
        [
            {"ticker": ticker, "weight": f"{entry['weight'] / total_weight:.12f}", **({"listings": entry["listings"]} if entry["listings"] else {}), **({"otc": entry["otc"]} if entry["otc"] else {})}
            for ticker, entry in combined.items()
        ],
        key=lambda x: float(x["weight"]),
        reverse=True,
    )
    blended_index = {
        "name": name,
        # reweight_to_present corrects for drift since as_of; most of the weight is US
        "as_of": us_data["as_of"],
        "components": normalized_components,
    }

    output_file = BASE / f"{output}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(blended_index, f, indent=2)

    print(f"Saved {output_file}")


def blend_all():
    for output, (name, us_ratio, intl_index) in BLENDS.items():
        blend(output, name, us_ratio, intl_index)


if __name__ == "__main__":
    blend_all()
