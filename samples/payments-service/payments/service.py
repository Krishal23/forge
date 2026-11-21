import uuid
from typing import Optional

from payments import auth
from payments.models import Payment


class PaymentError(Exception):
    pass


def create_payment(conn, merchant_id: str, amount: int, currency: str, card_number: str) -> Payment:
    if amount <= 0:
        raise PaymentError("amount must be positive")
    if not card_number.isdigit() or len(card_number) < 12:
        raise PaymentError("invalid card number")
    payment_id = "pay_" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO payments (id, merchant_id, amount, currency, status, card_last4) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (payment_id, merchant_id, amount, currency, "authorized", card_number[-4:]),
    )
    conn.commit()
    return get_payment(conn, payment_id)


def get_payment(conn, payment_id: str) -> Optional[Payment]:
    row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return Payment.from_row(row) if row else None


def capture_payment(conn, merchant_id: str, payment_id: str) -> Payment:
    payment = get_payment(conn, payment_id)
    if payment is None:
        raise PaymentError("payment not found")
    auth.assert_merchant_owns_payment(payment, merchant_id)
    if payment.status != "authorized":
        raise PaymentError(f"cannot capture payment in status {payment.status}")
    conn.execute("UPDATE payments SET status = ? WHERE id = ?", ("captured", payment_id))
    conn.commit()
    return get_payment(conn, payment_id)
