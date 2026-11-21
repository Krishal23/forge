"""Small helpers: hashing, time, and pulling a JSON object out of model text."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every hash in Forge (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    """Pull a JSON object out of model text.

    Accepts raw JSON, JSON inside a ```json fence, or JSON surrounded by prose.
    Raises ValueError if no parseable object is found. This is deliberately
    tolerant about *where* the JSON is and strict about *what* it is: schema
    validation happens afterwards.
    """
    if text is None:
        raise ValueError("empty model output")
    candidates: list[str] = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    decoder = json.JSONDecoder()
    if start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
            if isinstance(value, dict):
                candidates.append(json.dumps(value))
        except json.JSONDecodeError:
            pass
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"no JSON object found in model output ({last_error})")
