from .inventory import IndexInventory
from os.path import dirname

INDEXES = IndexInventory.from_path(dirname(__file__))


__all__ = ["INDEXES"]
