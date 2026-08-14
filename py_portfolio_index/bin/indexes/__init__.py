from os.path import dirname

from .inventory import IndexInventory

INDEXES = IndexInventory.from_path(dirname(__file__))


__all__ = ["INDEXES"]
