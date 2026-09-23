import pytest

from app.core.auth.secrets import (
    SECRET_PREFIX,
    digest,
    generate_secret,
    hash_secret,
    verify_secret,
)


def test_generate_secret_uses_prefix_and_is_unique():
    first = generate_secret(SECRET_PREFIX)
    second = generate_secret(SECRET_PREFIX)

    assert first.startswith(SECRET_PREFIX)
    assert second.startswith(SECRET_PREFIX)
    assert first != second


def test_hash_secret_does_not_contain_plaintext():
    plaintext = "sf_live_plaintext-secret"

    secret_hash = hash_secret(plaintext)

    assert secret_hash.startswith("$argon2")
    assert plaintext not in secret_hash


def test_hashing_the_same_secret_twice_gives_different_hashes():
    """Argon2 salts every hash, so a stolen table shows no two accounts sharing a password."""
    assert hash_secret("correct horse battery") != hash_secret("correct horse battery")


def test_verify_secret_accepts_matching_secret():
    plaintext = "sf_live_plaintext-secret"
    secret_hash = hash_secret(plaintext)

    assert verify_secret(plaintext, secret_hash)


def test_verify_secret_rejects_wrong_secret():
    secret_hash = hash_secret("sf_live_correct-secret")

    assert not verify_secret("sf_live_wrong-secret", secret_hash)


def test_verify_secret_rejects_malformed_hash():
    assert not verify_secret("sf_live_secret", "not-a-valid-hash")


def test_verify_secret_rejects_invalid_hash_parts():
    assert not verify_secret("sf_live_secret", "$argon2id$not-valid")


@pytest.mark.parametrize("nbytes", [0, -1])
def test_generate_secret_rejects_invalid_entropy_length(nbytes):
    with pytest.raises(ValueError, match="nbytes must be positive"):
        generate_secret(SECRET_PREFIX, nbytes=nbytes)


def test_digest_is_deterministic_sha256_hex():
    """An opaque token is looked up by its digest, so the same token must always give the same one."""
    assert digest("sf_ua_token") == digest("sf_ua_token")
    assert digest("sf_ua_token") != digest("sf_ua_other")
    assert len(digest("sf_ua_token")) == 64
    assert "sf_ua_token" not in digest("sf_ua_token")
