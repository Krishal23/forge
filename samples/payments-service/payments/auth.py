"""Authorization checks shared by payment and refund flows."""

from payments.models import Payment


class AuthorizationError(Exception):
    pass


def assert_merchant_owns_payment(payment: Payment, merchant_id: str) -> None:
    if payment.merchant_id != merchant_id:
        raise AuthorizationError("merchant does not own this payment")
