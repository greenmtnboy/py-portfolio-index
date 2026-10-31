import json
from pathlib import Path


# CONFIGURATION
def blend_us_eu():
    base = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes"
    us_file = base / "total_market.json"
    eu_file = base / "eu_index.json"
    us_ratio = 0.6
    eu_ratio = 0.4
    output_file = base / "blended_world_60_40.json"

    # Load both JSON files
    with open(us_file, "r", encoding="utf-8") as f:
        us_data = json.load(f)

    with open(eu_file, "r", encoding="utf-8") as f:
        eu_data = json.load(f)

    # every EU component carries its home listing, so the sleeve is the full 40%;
    # a provider that can trade none of a company's listings leaves its share in cash
    # rather than spreading it over the companies it can buy
    combined: dict[str, dict] = {}
    # any listing of a company finds it, so one also in the US index (CRH) is merged
    owner: dict[str, str] = {}
    for components, ratio in ((us_data["components"], us_ratio), (eu_data["components"], eu_ratio)):
        for comp in components:
            symbols = [comp["ticker"], *comp.get("listings", [])]
            key = next((owner[x] for x in symbols if x in owner), comp["ticker"])
            entry = combined.setdefault(key, {"weight": 0.0, "listings": []})
            entry["weight"] += float(comp["weight"]) * ratio
            for symbol in symbols:
                if symbol != key and symbol not in entry["listings"]:
                    entry["listings"].append(symbol)
                owner.setdefault(symbol, key)

    # Normalize so total weight = 1.0
    total_weight = sum(x["weight"] for x in combined.values())

    normalized_components = sorted(
        [
            {"ticker": ticker, "weight": f"{entry['weight'] / total_weight:.12f}", **({"listings": entry["listings"]} if entry["listings"] else {})}
            for ticker, entry in combined.items()
        ],
        key=lambda x: float(x["weight"]),
        reverse=True,
    )
    # Prepare final JSON
    blended_index = {
        "name": "Blended US+EU Index",
        # reweight_to_present corrects for drift since as_of; most of the weight is US
        "as_of": us_data["as_of"],
        "components": normalized_components,
    }

    # Write to file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(blended_index, f, indent=2)

    print(f"Saved {output_file}")


if __name__ == "__main__":
    blend_us_eu()
