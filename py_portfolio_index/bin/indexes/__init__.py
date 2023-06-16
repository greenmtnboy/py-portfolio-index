from .inventory import IndexInventory


INDEXES = IndexInventory.from_path(__file__)


__all__ = ["INDEXES"]