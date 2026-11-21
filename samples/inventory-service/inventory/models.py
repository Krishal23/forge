from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Item:
    sku: str
    name: str
    on_hand: int


@dataclass
class Reservation:
    id: str
    sku: str
    quantity: int
    created_at: float  # unix seconds


@dataclass
class InventoryStore:
    items: Dict[str, Item] = field(default_factory=dict)
    reservations: Dict[str, Reservation] = field(default_factory=dict)
