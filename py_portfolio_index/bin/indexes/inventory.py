from typing import Set
import re
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field
from pathlib import Path
import json

from py_portfolio_index.models import IdealPortfolioElement, IdealPortfolio

QUARTER_TO_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}


def parse_date_from_name(input: str) -> date | None:
    components = input.lower().split("_")
    year = None
    quarter = None
    for item in components:
        if re.match("[0-9]{4}", item):
            year = int(item)
        if re.match("q[1-4]", item):
            quarter = int(item[1])
    if not year or not quarter:
        return None

    return date(year=year, month=QUARTER_TO_MONTH[quarter], day=1)


class IndexInventory(BaseModel):
    csv_keys: Set[str] = Field(exclude=True)
    json_keys: Set[str] = Field(exclude=True)
    base: Path = Field(exclude=True)
    loaded: dict[str, IdealPortfolio] = Field(default_factory=dict)

    @property
    def keys(self) -> Set[str]:
        return self.csv_keys.union(self.json_keys)

    @classmethod
    def from_path(cls, path):
        path = Path(path)
        if path.is_file():
            path = path.parent
        json_keys = set()
        csv_keys = set()
        for f in path.iterdir():
            try:
                if f.suffix == ".csv":
                    csv_keys.add(f.stem)
                if f.suffix == ".json":
                    json_keys.add(f.stem)
            except FileNotFoundError:
                pass
        return IndexInventory(json_keys=json_keys, csv_keys=csv_keys, base=path)

    def __getitem__(self, item: str) -> IdealPortfolio:
        if item in self.keys:
            values = self.loaded.get(item, None)
            if values:
                return values
            values = self.get_values(item)
            self.loaded[item] = values
            return values
        raise KeyError(item)

    def get_values(self, item: str) -> IdealPortfolio:
        out = []
        start_date = None
        if item in self.json_keys:
            with open(self.base / f"{item}.json") as f:
                parsed = json.loads(f.read())
                start_date = date.fromisoformat(
                    parsed.get("as_of", date.today().isoformat())
                )
                for row in parsed.get("components", []):
                    out.append(
                        IdealPortfolioElement(
                            ticker=row["ticker"], weight=Decimal(row["weight"])
                        )
                    )
        elif item in self.csv_keys:
            with open(self.base / f"{item}.csv") as f:
                contents = f.read()
                for row in contents.split("\n"):
                    ticker, weight = row.split(",", 1)
                    start_date = parse_date_from_name(item)
                    out.append(
                        IdealPortfolioElement(ticker=ticker, weight=Decimal(weight))
                    )
        else:
            raise ValueError("No matching file {}".format(item))

        if start_date:
            return IdealPortfolio(holdings=out, source_date=start_date)
        return IdealPortfolio(holdings=out)
