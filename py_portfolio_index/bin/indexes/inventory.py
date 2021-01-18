from os import listdir
from os.path import dirname, join
from typing import List

from py_portfolio_index.models import IdealPortfolioElement, IdealPortfolio


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
        return IdealPortfolio(holdings=out)
