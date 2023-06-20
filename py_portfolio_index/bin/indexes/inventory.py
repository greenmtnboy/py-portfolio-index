from typing import Set
import re
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field
from pathlib import Path

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
    keys: Set[str] = Field(exclude=True)
    base: Path = Field(exclude=True)
    loaded: dict[str, IdealPortfolio] = Field(default_factory=dict)

    @classmethod
    def from_path(cls, path):
        path = Path(path)
        if path.is_file():
            path = path.parent
        keys = []
        for f in path.iterdir():
            try:
                if f.suffix == ".csv":
                    keys.append(f.stem)
            except FileNotFoundError:
                pass
        return IndexInventory(keys=keys, base=path)

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
        with open(self.base / f"{item}.csv") as f:
            contents = f.read()
            for row in contents.split("\n"):
                ticker, weight = row.split(",", 1)
                out.append(IdealPortfolioElement(ticker=ticker, weight=Decimal(weight)))
        start_date = parse_date_from_name(item)
        if start_date:
            return IdealPortfolio(holdings=out, source_date=start_date)
        return IdealPortfolio(holdings=out)
