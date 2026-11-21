import pytest

from inventory.catalog import add_item
from inventory.models import InventoryStore


@pytest.fixture
def store():
    s = InventoryStore()
    add_item(s, "SKU-1", "Card reader", 10)
    return s
