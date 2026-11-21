import pytest

from api.handlers import handle_refund
from payments.auth import AuthorizationError
from payments.service import create_payment, get_payment
from refunds.repository import find_refunds_for_merchant
from refunds.service import RefundError, issue_refund


def test_full_refund_marks_payment_refunded(conn, captured_payment):
    refund = issue_refund(conn, "m_1", captured_payment.id)
    assert refund.amount == 10_000
    assert get_payment(conn, captured_payment.id).status == "refunded"


def test_cannot_refund_twice(conn, captured_payment):
    issue_refund(conn, "m_1", captured_payment.id)
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", captured_payment.id)


def test_refund_requires_owner(conn, captured_payment):
    with pytest.raises(AuthorizationError):
        issue_refund(conn, "m_2", captured_payment.id)


def test_cannot_refund_uncaptured_payment(conn):
    payment = create_payment(conn, "m_1", 500, "INR", "4111111111111111")
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", payment.id)


def test_handler_returns_201(conn, captured_payment):
    response = handle_refund(conn, {"merchant_id": "m_1", "payment_id": captured_payment.id})
    assert response["status"] == 201
    assert response["body"]["amount"] == 10_000


def test_handler_rejects_missing_field(conn):
    assert handle_refund(conn, {"merchant_id": "m_1"})["status"] == 400


def test_merchant_report(conn, captured_payment):
    issue_refund(conn, "m_1", captured_payment.id)
    assert len(find_refunds_for_merchant(conn, "m_1")) == 1
