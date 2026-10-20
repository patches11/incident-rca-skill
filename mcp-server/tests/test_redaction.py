import json

from redaction import redact, redact_text


def test_redacts_common_secrets_and_pii():
    text = (
        "user jane.doe@example.com paid with 4111 1111 1111 1111 using sk_live_9f8e7d6c5b4a3f2e1d0c "
        "AKIAIOSFODNN7EXAMPLE postgres://svc:hunter2@db:5432/payments password=s3cret "
        "Authorization: Bearer abcdefghijklmnop1234"
    )
    out = redact_text(text)
    for leaked in ["jane.doe@example.com", "4111 1111 1111 1111", "sk_live_", "AKIAIOSFODNN7EXAMPLE", "hunter2", "s3cret", "abcdefghijklmnop1234"]:
        assert leaked not in out, leaked


def test_keeps_operational_numbers():
    # Card regex must not eat timestamps, durations or non-Luhn numbers.
    text = "QueuePool limit of size 20 overflow 0 reached, timeout 1.00 at 1760434980123 order 1234567890123"
    assert redact_text(text) == text


def test_sensitive_keys_dropped_recursively():
    out = redact({"a": [{"authorization": "anything", "msg": "ok"}], "customer_email": "x@y.io"})
    assert out["a"][0]["authorization"] == "[REDACTED:SECRET]"
    assert "x@y.io" not in json.dumps(out)
