import hashlib

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


def test_generate_secret_takes_any_prefix():
    assert generate_secret("sf_ua_").startswith("sf_ua_")


def test_hash_secret_does_not_contain_plaintext():
    plaintext = "sf_live_plaintext-secret"

    secret_hash = hash_secret(plaintext)

    assert secret_hash.startswith("$argon2")
    assert plaintext not in secret_hash


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


def test_digest_is_the_deterministic_sha256_hex_of_the_token():
    token = "sf_ua_plaintext-token"

    assert digest(token) == digest(token) == hashlib.sha256(token.encode()).hexdigest()
    assert token not in digest(token)
    assert digest(token) != digest("sf_ua_other-token")


def test_a_token_that_is_no_text_has_a_digest_nothing_matches():
    """A lone surrogate is valid JSON but no UTF-8; its digest exists and equals no generated token's."""
    assert digest("sf_ua_\ud800") != digest("sf_ua_")
    assert len(digest("sf_ua_\ud800")) == 64
