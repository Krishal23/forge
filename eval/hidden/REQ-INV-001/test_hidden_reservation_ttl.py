"""Hidden acceptance tests for REQ-INV-001. Never shown to agents."""

import pytest

from inventory.catalog import add_item, available
from inventory.models import InventoryStore
from inventory.reservations import ReservationError, commit, expire_stale, reserve


@pytest.fixture
def store():
    s = InventoryStore()
    add_item(s, "SKU-1", "Card reader", 10)
    return s


def test_default_ttl_is_900():
    assert InventoryStore().ttl_seconds == 900


def test_reservation_counts_before_expiry(store):
    reserve(store, "SKU-1", 3, now=1000.0)
    assert available(store, "SKU-1", now=1000.0 + 899) == 7


def test_reservation_ignored_at_expiry_boundary(store):
    reserve(store, "SKU-1", 3, now=1000.0)
    assert available(store, "SKU-1", now=1000.0 + 900) == 10


def test_available_without_now_unchanged(store):
    reserve(store, "SKU-1", 3, now=1000.0)
    assert available(store, "SKU-1") == 7


def test_custom_ttl():
    s = InventoryStore(ttl_seconds=60)
    add_item(s, "SKU-1", "x", 5)
    reserve(s, "SKU-1", 5, now=0.0)
    assert available(s, "SKU-1", now=59.0) == 0
    assert available(s, "SKU-1", now=60.0) == 5


def test_commit_expired_rejected(store):
    r = reserve(store, "SKU-1", 2, now=1000.0)
    with pytest.raises(ReservationError):
        commit(store, r.id, now=1000.0 + 900)
    assert store.items["SKU-1"].on_hand == 10


def test_commit_fresh_ok(store):
    r = reserve(store, "SKU-1", 2, now=1000.0)
    commit(store, r.id, now=1000.0 + 10)
    assert store.items["SKU-1"].on_hand == 8


def test_expire_stale_removes_and_counts(store):
    reserve(store, "SKU-1", 1, now=0.0)
    reserve(store, "SKU-1", 1, now=100.0)
    keep = reserve(store, "SKU-1", 1, now=950.0)
    assert expire_stale(store, now=1000.0) == 2
    assert list(store.reservations) == [keep.id]


def test_reserve_uses_unexpired_stock(store):
    reserve(store, "SKU-1", 10, now=0.0)
    # The first reservation has expired, so stock is available again.
    reserve(store, "SKU-1", 10, now=900.0)
