import pytest

from payments.auth import AuthorizationError
from payments.service import PaymentError, capture_payment, create_payment, get_payment


def test_create_payment_stores_only_last4(conn):
    payment = create_payment(conn, "m_1", 500, "INR", "4111111111111111")
    assert payment.status == "authorized"
    assert payment.card_last4 == "1111"


def test_create_payment_rejects_non_positive_amount(conn):
    with pytest.raises(PaymentError):
        create_payment(conn, "m_1", 0, "INR", "4111111111111111")


def test_capture_requires_owner(conn):
    payment = create_payment(conn, "m_1", 500, "INR", "4111111111111111")
    with pytest.raises(AuthorizationError):
        capture_payment(conn, "m_2", payment.id)


def test_capture_changes_status(conn):
    payment = create_payment(conn, "m_1", 500, "INR", "4111111111111111")
    capture_payment(conn, "m_1", payment.id)
    assert get_payment(conn, payment.id).status == "captured"
