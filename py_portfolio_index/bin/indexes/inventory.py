from os import listdir
from os.path import dirname, join
from typing import List
import re
from datetime import date

from py_portfolio_index.models import IdealPortfolioElement, IdealPortfolio

QUARTER_TO_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}


def parse_date_from_name(input: str):
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


class IndexInventory(object):
    def __init__(self):
        self.keys: List[str] = []
        base = dirname(__file__)
        self.keys = [f.split(".")[0] for f in listdir(base) if f.endswith(".csv")]
        self.loaded = {}

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
        with open(join(dirname(__file__), f"{item}.csv")) as f:
            contents = f.read()
            for row in contents.split("\n"):
                ticker, weight = row.split(",", 1)
                out.append(IdealPortfolioElement(ticker=ticker, weight=float(weight)))
        start_date = parse_date_from_name(item)
        if start_date:
            return IdealPortfolio(holdings=out, source_date=start_date)
        return IdealPortfolio(holdings=out)
