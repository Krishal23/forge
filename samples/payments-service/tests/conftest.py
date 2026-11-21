import pytest

from payments.db import connect
from payments.service import capture_payment, create_payment


@pytest.fixture
def conn():
    c = connect()
    yield c
    c.close()


@pytest.fixture
def captured_payment(conn):
    payment = create_payment(conn, "m_1", 10_000, "INR", "4111111111111111")
    return capture_payment(conn, "m_1", payment.id)
