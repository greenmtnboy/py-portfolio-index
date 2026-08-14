from os.path import dirname

from .inventory import StocklistInventory

STOCK_LISTS = StocklistInventory.from_path(dirname(__file__))
