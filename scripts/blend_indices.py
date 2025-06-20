import json
from collections import defaultdict
from datetime import date
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

    # Helper to convert weights to float and apply ratio
    def scale_components(components, ratio):
        return {comp["ticker"]: float(comp["weight"]) * ratio for comp in components}

    # Scale weights by their respective ratios
    us_scaled = scale_components(us_data["components"], us_ratio)
    eu_scaled = scale_components(eu_data["components"], eu_ratio)

    # Combine the tickers and sum weights if duplicate
    combined_weights = defaultdict(float)

    for ticker, weight in us_scaled.items():
        combined_weights[ticker] += weight

    for ticker, weight in eu_scaled.items():
        combined_weights[ticker] += weight

    # Normalize so total weight = 1.0
    total_weight = sum(combined_weights.values())

    normalized_components = sorted(
        [
            {"ticker": ticker, "weight": f"{weight / total_weight:.12f}"}
            for ticker, weight in combined_weights.items()
        ],
        key=lambda x: float(x["weight"]),
        reverse=True,
    )
    # Prepare final JSON
    blended_index = {
        "name": "Blended US+EU Index",
        "as_of": str(date.today()),
        "components": normalized_components,
    }

    # Write to file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(blended_index, f, indent=2)

    print(f"Saved {output_file}")


if __name__ == "__main__":
    blend_us_eu()
