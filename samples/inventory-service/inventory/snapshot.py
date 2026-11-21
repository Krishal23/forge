"""Save and load store snapshots for warm restarts."""

import pickle

from inventory.models import InventoryStore


def save(store: InventoryStore, path: str) -> None:
    with open(path, "wb") as fh:
        pickle.dump(store, fh)


def load(path: str) -> InventoryStore:
    with open(path, "rb") as fh:
        return pickle.load(fh)
