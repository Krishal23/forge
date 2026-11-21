from dataclasses import dataclass
from typing import Optional


@dataclass
class Payment:
    id: str
    merchant_id: str
    amount: int
    currency: str
    status: str
    card_last4: str

    @classmethod
    def from_row(cls, row) -> "Payment":
        return cls(
            id=row["id"],
            merchant_id=row["merchant_id"],
            amount=row["amount"],
            currency=row["currency"],
            status=row["status"],
            card_last4=row["card_last4"],
        )


@dataclass
class Refund:
    id: str
    payment_id: str
    amount: int
    idempotency_key: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row) -> "Refund":
        return cls(
            id=row["id"],
            payment_id=row["payment_id"],
            amount=row["amount"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
        )
