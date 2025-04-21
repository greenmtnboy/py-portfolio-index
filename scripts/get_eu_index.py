import requests
from datetime import date
import json

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
    components = [
        {
            "ticker": entry["mnemo"],
            "weight": f"{entry['value'] / total_value:.12f}"
        }
        for entry in stock_entries
    ]

    # Construct the final JSON structure
    output = {
        "name": "EU Index",
        "as_of": str(date.today()),
        "components": components
    }

    # Save to JSON file
    with open("eu_index.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("Saved eu_index.json")

if __name__ == "__main__":
    get_eu_index()