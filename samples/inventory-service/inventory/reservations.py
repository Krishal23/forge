"""Stock reservations held during checkout."""

import uuid

from inventory.catalog import available
from inventory.models import InventoryStore, Reservation


class ReservationError(Exception):
    pass


def reserve(store: InventoryStore, sku: str, quantity: int, now: float) -> Reservation:
    if quantity <= 0:
        raise ReservationError("quantity must be positive")
    if available(store, sku) < quantity:
        raise ReservationError("insufficient stock")
    reservation = Reservation(id="res_" + uuid.uuid4().hex[:10], sku=sku, quantity=quantity, created_at=now)
    store.reservations[reservation.id] = reservation
    return reservation


def release(store: InventoryStore, reservation_id: str) -> None:
    if reservation_id not in store.reservations:
        raise ReservationError("unknown reservation")
    del store.reservations[reservation_id]


def commit(store: InventoryStore, reservation_id: str) -> None:
    """Checkout completed: deduct stock permanently."""
    reservation = store.reservations.pop(reservation_id, None)
    if reservation is None:
        raise ReservationError("unknown reservation")
    store.items[reservation.sku].on_hand -= reservation.quantity
