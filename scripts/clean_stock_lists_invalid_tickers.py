from py_portfolio_index.bin import INDEXES, STOCK_LISTS, VALID_STOCKS
from py_portfolio_index import AlpacaProvider
from alpaca.common.exceptions import APIError
from pathlib import Path
def remove_ticker_from_index(index, ticker):
    root = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "indexes"
    target = root / f"{index}.csv"
    with open(target , "r") as f:
        lines = f.read().split("\n")

    final = [l for l in lines if l.split(",")[0] != ticker and l.strip()]
    with open(target , "w") as f:
        f.write("\n".join(final))

def remove_ticker_from_list(index, ticker):
    root = Path(__file__).parent.parent / "py_portfolio_index" / "bin" / "lists"
    target = root / f"{index}.csv"
    with open(target , "r") as f:
        lines = f.read().split("\n")

    final = [l for l in lines if l.split(",")[0] != ticker and l.strip()]
    with open(target , "w") as f:
        f.write("\n".join(final))

if __name__ == "__main__":
    local = AlpacaProvider()
    CONFIRMED = set()

    for key in INDEXES.keys:
 
        for member in INDEXES[key].holdings:
            if not member.ticker.strip():
                continue
            if member.ticker not in VALID_STOCKS:
                try:
                    if member.ticker in CONFIRMED:
                        raise APIError('fake error')
                    test = local.get_stock_info(member.ticker)
                except APIError as e:
                    print(e)
                    CONFIRMED.add(member.ticker)
                    # print(test)
                    print(f"Index {key} has invalid ticker {member.ticker}")
                    remove_ticker_from_index(key, member.ticker)

    for key in STOCK_LISTS.keys:

        for item in STOCK_LISTS[key]:
            if not item.strip():
                continue
            if item not in VALID_STOCKS:
                try:
                    if item in CONFIRMED:
                        raise APIError('fake error')
                    test = local.get_stock_info(item)
                except APIError as e:
                    print(e)
                    CONFIRMED.add(item)
                    # print(test)
                    print(f"List {key} has invalid ticker {item}")
                    remove_ticker_from_list(key, item)