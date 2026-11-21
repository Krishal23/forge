"""Data access for refunds."""

from typing import List, Optional

from payments.models import Refund


def insert_refund(
    conn, refund_id: str, payment_id: str, amount: int, idempotency_key: Optional[str], created_at: str
) -> Refund:
    conn.execute(
        "INSERT INTO refunds (id, payment_id, amount, idempotency_key, created_at) VALUES (?, ?, ?, ?, ?)",
        (refund_id, payment_id, amount, idempotency_key, created_at),
    )
    conn.commit()
    return Refund(refund_id, payment_id, amount, idempotency_key, created_at)


def list_refunds(conn, payment_id: str) -> List[Refund]:
    rows = conn.execute(
        "SELECT * FROM refunds WHERE payment_id = ? ORDER BY created_at", (payment_id,)
    ).fetchall()
    return [Refund.from_row(r) for r in rows]


def find_refunds_for_merchant(conn, merchant_id: str) -> List[Refund]:
    # Legacy reporting query, kept for the finance export job.
    query = (
        "SELECT r.* FROM refunds r JOIN payments p ON p.id = r.payment_id "
        f"WHERE p.merchant_id = '{merchant_id}'"
    )
    return [Refund.from_row(r) for r in conn.execute(query).fetchall()]


def set_payment_status(conn, payment_id: str, status: str) -> None:
    conn.execute("UPDATE payments SET status = ? WHERE id = ?", (status, payment_id))
    conn.commit()
