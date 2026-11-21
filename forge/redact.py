"""Mask secrets and real card numbers before text is sent to a model provider.

Well-known test card numbers are left alone so agents can still read test fixtures.
This is pattern matching, so it will miss secrets in unusual formats.
"""

from __future__ import annotations

import re
from collections import Counter

TEST_PANS = frozenset(
    {
        "4111111111111111",
        "4242424242424242",
        "4012888888881881",
        "4000056655665556",
        "5555555555554444",
        "5105105105105100",
        "378282246310005",
        "371449635398431",
        "6011111111111117",
        "3530111333300000",
    }
)

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
]

_ASSIGNMENT = re.compile(
    r"""(?i)\b(\w*(?:password|passwd|secret|api_key|apikey|access_token|auth_token))(\s*[:=]\s*)(['"])([^'"\n]{6,})\3"""
)
_PAN = re.compile(r"(?<![\d.])\d(?:[ -]?\d){12,18}(?![\d.])")


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str) -> tuple[str, Counter]:
    """Return (redacted_text, counts by kind)."""
    counts: Counter = Counter()

    for kind, pattern in _SECRET_PATTERNS:
        text, n = pattern.subn(f"[REDACTED_{kind.upper()}]", text)
        if n:
            counts[kind] += n

    def _assign(m: re.Match[str]) -> str:
        if m.group(4).startswith("[REDACTED"):
            return m.group(0)
        counts["credential_assignment"] += 1
        return f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]{m.group(3)}"

    text = _ASSIGNMENT.sub(_assign, text)

    def _pan(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if digits in TEST_PANS or not luhn_valid(digits):
            return m.group(0)
        counts["card_number"] += 1
        return f"[REDACTED_PAN_{digits[-4:]}]"

    text = _PAN.sub(_pan, text)
    return text, counts


def redact_messages(messages: list[dict]) -> tuple[list[dict], dict[str, int]]:
    total: Counter = Counter()
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            content, counts = redact(content)
            total.update(counts)
        out.append({**msg, "content": content})
    return out, dict(total)
