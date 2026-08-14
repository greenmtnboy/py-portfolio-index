from os.path import dirname, join
from pathlib import Path

from pydantic import BaseModel, Field


class StocklistInventory(BaseModel):
    keys: set[str] = Field(exclude=True)
    base: Path = Field(exclude=True)
    loaded: dict[str, list[str]] = Field(default_factory=dict)

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
        return StocklistInventory(keys=keys, base=path)

    def __getitem__(self, item: str) -> list[str]:
        if item in self.keys:
            values = self.loaded.get(item, None)
            if values:
                return values
            values = self.get_values(item)
            self.loaded[item] = values
            return values
        raise KeyError(item)

    def get_values(self, item: str) -> list[str]:
        out = []
        with open(join(dirname(__file__), f"{item}.csv")) as f:
            contents = f.read()
            for row in contents.split("\n"):
                ticker = row.strip()
                out.append(ticker)
        return out

    def add_list(self, key: str, ticker_list: list[str]):
        """Add a new list or merge into existing list."""
        self.keys.add(key)
        current = self.loaded.get(key, [])
        self.loaded[key] = current + ticker_list
