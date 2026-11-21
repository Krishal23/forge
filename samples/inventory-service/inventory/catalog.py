from inventory.models import InventoryStore, Item


class CatalogError(Exception):
    pass


def add_item(store: InventoryStore, sku: str, name: str, on_hand: int) -> Item:
    if on_hand < 0:
        raise CatalogError("on_hand cannot be negative")
    if sku in store.items:
        raise CatalogError(f"sku {sku} already exists")
    item = Item(sku=sku, name=name, on_hand=on_hand)
    store.items[sku] = item
    return item


def reserved_quantity(store: InventoryStore, sku: str) -> int:
    return sum(r.quantity for r in store.reservations.values() if r.sku == sku)


def available(store: InventoryStore, sku: str) -> int:
    if sku not in store.items:
        raise CatalogError(f"unknown sku {sku}")
    return store.items[sku].on_hand - reserved_quantity(store, sku)
