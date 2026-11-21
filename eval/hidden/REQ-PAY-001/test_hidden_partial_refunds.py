"""Hidden acceptance tests for REQ-PAY-001. Never shown to agents.
Forge copies this file into the run's worktree only during evaluation."""

import pytest

from api.handlers import handle_refund
from payments.auth import AuthorizationError
from payments.db import connect
from payments.service import capture_payment, create_payment, get_payment
from refunds.repository import list_refunds
from refunds.service import RefundError, issue_refund


@pytest.fixture
def conn():
    c = connect()
    yield c
    c.close()


@pytest.fixture
def paid(conn):
    p = create_payment(conn, "m_1", 10_000, "INR", "4111111111111111")
    return capture_payment(conn, "m_1", p.id)


def test_partial_refund_sets_partially_refunded(conn, paid):
    r = issue_refund(conn, "m_1", paid.id, amount=3_000)
    assert r.amount == 3_000
    assert get_payment(conn, paid.id).status == "partially_refunded"


def test_refunds_summing_to_total_set_refunded(conn, paid):
    issue_refund(conn, "m_1", paid.id, amount=4_000)
    issue_refund(conn, "m_1", paid.id, amount=6_000)
    assert get_payment(conn, paid.id).status == "refunded"


def test_over_refund_rejected(conn, paid):
    issue_refund(conn, "m_1", paid.id, amount=7_000)
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", paid.id, amount=3_001)


def test_default_amount_is_remaining(conn, paid):
    issue_refund(conn, "m_1", paid.id, amount=2_500)
    r = issue_refund(conn, "m_1", paid.id)
    assert r.amount == 7_500
    assert get_payment(conn, paid.id).status == "refunded"


@pytest.mark.parametrize("bad", [0, -100])
def test_non_positive_amount_rejected(conn, paid, bad):
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", paid.id, amount=bad)


def test_idempotency_key_returns_original(conn, paid):
    first = issue_refund(conn, "m_1", paid.id, amount=1_000, idempotency_key="k-1")
    again = issue_refund(conn, "m_1", paid.id, amount=1_000, idempotency_key="k-1")
    assert again.id == first.id
    assert len(list_refunds(conn, paid.id)) == 1


def test_fully_refunded_payment_rejects_more(conn, paid):
    issue_refund(conn, "m_1", paid.id)
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", paid.id, amount=1)


def test_ownership_check_preserved(conn, paid):
    with pytest.raises(AuthorizationError):
        issue_refund(conn, "m_2", paid.id, amount=100)


def test_uncaptured_still_rejected(conn):
    p = create_payment(conn, "m_1", 500, "INR", "4111111111111111")
    with pytest.raises(RefundError):
        issue_refund(conn, "m_1", p.id, amount=100)


def test_handler_accepts_amount_and_key(conn, paid):
    resp = handle_refund(
        conn, {"merchant_id": "m_1", "payment_id": paid.id, "amount": 2_000, "idempotency_key": "h-1"}
    )
    assert resp["status"] == 201
    assert resp["body"]["amount"] == 2_000
    again = handle_refund(
        conn, {"merchant_id": "m_1", "payment_id": paid.id, "amount": 2_000, "idempotency_key": "h-1"}
    )
    assert again["body"]["refund_id"] == resp["body"]["refund_id"]


def test_handler_rejects_over_refund(conn, paid):
    resp = handle_refund(conn, {"merchant_id": "m_1", "payment_id": paid.id, "amount": 20_000})
    assert resp["status"] == 422
