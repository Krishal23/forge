import pytest

from api.handlers import handle_availability, handle_reserve
from inventory.catalog import CatalogError, add_item, available
from inventory.reservations import ReservationError, commit, release, reserve


def test_reserve_reduces_available(store):
    reserve(store, "SKU-1", 3, now=1000.0)
    assert available(store, "SKU-1") == 7


def test_cannot_over_reserve(store):
    with pytest.raises(ReservationError):
        reserve(store, "SKU-1", 11, now=1000.0)


def test_release_returns_stock(store):
    r = reserve(store, "SKU-1", 4, now=1000.0)
    release(store, r.id)
    assert available(store, "SKU-1") == 10


def test_commit_deducts_on_hand(store):
    r = reserve(store, "SKU-1", 4, now=1000.0)
    commit(store, r.id)
    assert store.items["SKU-1"].on_hand == 6
    assert available(store, "SKU-1") == 6


def test_duplicate_sku_rejected(store):
    with pytest.raises(CatalogError):
        add_item(store, "SKU-1", "dup", 1)


def test_handlers(store):
    assert handle_reserve(store, {"sku": "SKU-1", "quantity": 2}, now=1000.0)["status"] == 201
    assert handle_availability(store, "SKU-1")["body"]["available"] == 8
    assert handle_availability(store, "NOPE")["status"] == 404
