from os import listdir
from os.path import dirname, join
from typing import List


class StocklistInventory(object):
    def __init__(self):
        self.keys: List[str] = []
        base = dirname(__file__)
        self.keys = [f.split(".")[0] for f in listdir(base) if f.endswith(".csv")]
        self.loaded = {}

    def __getitem__(self, item: str) -> List[str]:
        if item in self.keys:
            values = self.loaded.get(item, None)
            if values:
                return values
            values = self.get_values(item)
            self.loaded[item] = values
            return values
        raise KeyError(item)

    def get_values(self, item: str) -> List[str]:
        out = []
        with open(join(dirname(__file__), f"{item}.csv")) as f:
            contents = f.read()
            for row in contents.split("\n"):
                ticker = row.strip()
                out.append(ticker)
        return out

    def add_list(self, key: str, ticker_list: List[str]):
        self.keys.append(key)
        self.loaded[key] = ticker_list
