from .inventory import StocklistInventory
from os.path import dirname

STOCK_LISTS = StocklistInventory.from_path(dirname(__file__))
