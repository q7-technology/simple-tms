from tms.services.signature import sign, verify


def test_signature_is_sha256_hex_of_the_raw_body():
    body = b'{"event_id":"x"}'
    good = sign("s3cret", body)
    assert good.startswith("sha256=") and len(good) == len("sha256=") + 64
    assert verify("s3cret", body, good)


def test_anything_else_does_not_verify():
    body = b'{"event_id":"x"}'
    good = sign("s3cret", body)
    assert not verify("s3cret", body + b" ", good)
    assert not verify("other", body, good)
    assert not verify("s3cret", body, None)
    assert not verify("", body, good)
    assert not verify("s3cret", body, "sha256=é")


def test_the_comparison_is_constant_time(monkeypatch):
    import hmac

    from tms.services import signature

    seen = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(signature.hmac, "compare_digest", spy)
    body = b'{"event_id":"x"}'
    assert verify("s3cret", body, sign("s3cret", body))
    assert not verify("s3cret", body, "sha256=" + "0" * 64)
    assert len(seen) == 2
