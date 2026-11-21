"""Thin request handlers. A real service would sit behind an HTTP framework;
here each handler takes a dict and returns {"status": int, "body": dict}."""

from payments.auth import AuthorizationError
from refunds.service import RefundError, issue_refund


def handle_refund(conn, request: dict) -> dict:
    try:
        refund = issue_refund(conn, request["merchant_id"], request["payment_id"])
    except KeyError as exc:
        return {"status": 400, "body": {"error": f"missing field {exc.args[0]}"}}
    except AuthorizationError as exc:
        return {"status": 403, "body": {"error": str(exc)}}
    except RefundError as exc:
        return {"status": 422, "body": {"error": str(exc)}}
    return {"status": 201, "body": {"refund_id": refund.id, "amount": refund.amount}}
