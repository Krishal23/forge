# inventory-service (Forge sample target)

An in-memory stock and reservation service, used to show Forge on a second codebase.
Standard library only.

- `inventory/catalog.py` items and available-stock calculation
- `inventory/reservations.py` reserve, release and commit stock during checkout
- `inventory/snapshot.py` pickle snapshots (an existing bandit finding, B301/B403)
- `api/handlers.py` request handlers

Run the tests: `python -m pytest -q`
