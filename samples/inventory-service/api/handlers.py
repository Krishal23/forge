from inventory.catalog import CatalogError, available
from inventory.reservations import ReservationError, reserve


def handle_reserve(store, request: dict, now: float) -> dict:
    try:
        reservation = reserve(store, request["sku"], int(request["quantity"]), now)
    except KeyError as exc:
        return {"status": 400, "body": {"error": f"missing field {exc.args[0]}"}}
    except (ReservationError, CatalogError) as exc:
        return {"status": 409, "body": {"error": str(exc)}}
    return {"status": 201, "body": {"reservation_id": reservation.id}}


def handle_availability(store, sku: str) -> dict:
    try:
        return {"status": 200, "body": {"sku": sku, "available": available(store, sku)}}
    except CatalogError as exc:
        return {"status": 404, "body": {"error": str(exc)}}
