import pytest

from forge.redact import luhn_valid, redact, redact_messages


def _luhn_complete(prefix: str) -> str:
    for d in "0123456789":
        if luhn_valid(prefix + d):
            return prefix + d
    raise AssertionError


REAL_LOOKING_PAN = _luhn_complete("453914880343646")


def test_real_pan_is_masked_keeping_last4():
    text, counts = redact(f"charged card {REAL_LOOKING_PAN} today")
    assert REAL_LOOKING_PAN not in text
    assert f"[REDACTED_PAN_{REAL_LOOKING_PAN[-4:]}]" in text
    assert counts["card_number"] == 1


def test_pan_with_spaces_is_masked():
    spaced = " ".join(REAL_LOOKING_PAN[i : i + 4] for i in range(0, 16, 4))
    text, _ = redact(spaced)
    assert "REDACTED_PAN" in text


@pytest.mark.parametrize("pan", ["4111111111111111", "4242424242424242", "5555555555554444"])
def test_test_pans_are_kept(pan):
    assert redact(f'create_payment(conn, "m_1", 500, "INR", "{pan}")')[0].count(pan) == 1


def test_non_luhn_numbers_and_amounts_kept():
    for s in ["order 1234567890123456", "amount = 10_000", "ts 1732140000.123"]:
        assert redact(s)[0] == s


@pytest.mark.parametrize(
    "secret,kind",
    [
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("AIza" + "A" * 35, "google_api_key"),
        ("sk-" + "a" * 32, "openai_style_key"),
        ("gsk_" + "b" * 40, "groq_key"),
        ("ghp_" + "c" * 36, "github_token"),
    ],
)
def test_key_formats_masked(secret, kind):
    text, counts = redact(f"x = {secret}")
    assert secret not in text and counts[kind] == 1


def test_credential_assignment_masked_but_key_name_kept():
    text, counts = redact('DB_PASSWORD = "hunter2-very-secret"\napi_key: "abcdefgh"')
    assert "hunter2" not in text and "abcdefgh" not in text
    assert 'PASSWORD = "[REDACTED]"' in text and counts["credential_assignment"] == 2


def test_private_key_block_masked():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact(pem)[0]


def test_redact_messages_preserves_structure():
    msgs = [{"role": "system", "content": "safe"}, {"role": "user", "content": "AKIAIOSFODNN7EXAMPLE"}]
    out, counts = redact_messages(msgs)
    assert out[0] == msgs[0] and out[1]["role"] == "user" and counts == {"aws_access_key": 1}
    assert msgs[1]["content"] == "AKIAIOSFODNN7EXAMPLE"  # input not mutated
