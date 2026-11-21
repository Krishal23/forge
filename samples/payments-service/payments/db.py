"""SQLite connection and schema for the payments service.

Amounts are stored as integers in minor currency units (paise for INR).
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    card_last4 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments(id),
    amount INTEGER NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
