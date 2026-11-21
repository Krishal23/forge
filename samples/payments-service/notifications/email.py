"""Merchant notifications. Out of scope for most refund changes."""

import logging

logger = logging.getLogger(__name__)


def notify_refund(merchant_email: str, refund_id: str, amount: int) -> None:
    # Delivery is stubbed: a real implementation would enqueue an email job.
    logger.info("refund %s of %d queued for %s", refund_id, amount, merchant_email)
