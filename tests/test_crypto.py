"""cryptography helpers: AES-EAX roundtrips, tamper detection, salted hashing."""

import pytest

from forest import cryptography


def test_roundtrip_str() -> None:
    ct = cryptography.get_ciphertext_value("secret value")
    assert cryptography.get_cleartext_value(ct) == "secret value"


def test_roundtrip_bytes() -> None:
    ct = cryptography.get_ciphertext_value(b"secret bytes")
    assert cryptography.get_cleartext_value(ct) == "secret bytes"


def test_roundtrip_unicode() -> None:
    ct = cryptography.get_ciphertext_value("⛧ faeries 🌲")
    assert cryptography.get_cleartext_value(ct) == "⛧ faeries 🌲"


def test_nonce_varies() -> None:
    assert cryptography.get_ciphertext_value("x") != cryptography.get_ciphertext_value(
        "x"
    )


def test_tampering_detected() -> None:
    import base58

    raw = bytearray(base58.b58decode(cryptography.get_ciphertext_value("payload")))
    raw[-1] ^= 0xFF  # flip a bit in the ciphertext
    tampered = base58.b58encode(bytes(raw)).decode()
    with pytest.raises(ValueError):
        cryptography.get_cleartext_value(tampered)


def test_bad_value_type_rejected() -> None:
    with pytest.raises(ValueError):
        cryptography.get_ciphertext_value(42)  # type: ignore[arg-type]


def test_hash_salt_deterministic() -> None:
    assert cryptography.hash_salt("key") == cryptography.hash_salt("key")
    assert cryptography.hash_salt("key") != cryptography.hash_salt("other")
    assert cryptography.hash_salt("key", salt="a") != cryptography.hash_salt(
        "key", salt="b"
    )


def test_secrets_validated_lazily(monkeypatch) -> None:
    """no AESKEY/SALT is only fatal when a persistence primitive is used, not at import"""
    monkeypatch.setattr(cryptography.utils, "get_secret", lambda key, env=None: "")
    cryptography.get_aeskey.cache_clear()
    cryptography.get_salt.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="AESKEY"):
            cryptography.get_ciphertext_value("x")
        with pytest.raises(RuntimeError, match="SALT"):
            cryptography.hash_salt("x")
    finally:
        cryptography.get_aeskey.cache_clear()
        cryptography.get_salt.cache_clear()
