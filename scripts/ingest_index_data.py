
if __name__ == "__main__":
    from pathlib import Path
    import csv
    from io import StringIO
    import requests
    from datetime import date, datetime

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
        indexes[row[2]].append(f"{row[-3]},{row[-1]}")
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
