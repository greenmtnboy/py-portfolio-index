from py_portfolio_index import INDEXES, STOCK_LISTS


def test_indexes():
    for key in INDEXES.keys:
        x = INDEXES[key]

        print(x.json())
    print(INDEXES.json())


def test_lists():
    for key in STOCK_LISTS.keys:
        STOCK_LISTS[key]

    print(STOCK_LISTS.json())
