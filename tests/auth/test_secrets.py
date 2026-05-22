import pytest

from app.core.auth.secrets import (
    SECRET_PREFIX,
    generate_client_secret,
    hash_client_secret,
    verify_client_secret,
)


def test_generate_client_secret_uses_prefix_and_is_unique():
    first = generate_client_secret()
    second = generate_client_secret()

    assert first.startswith(SECRET_PREFIX)
    assert second.startswith(SECRET_PREFIX)
    assert first != second


def test_hash_client_secret_does_not_contain_plaintext():
    plaintext = "sf_live_plaintext-secret"

    secret_hash = hash_client_secret(plaintext)

    assert secret_hash.startswith("$argon2")
    assert plaintext not in secret_hash


def test_verify_client_secret_accepts_matching_secret():
    plaintext = "sf_live_plaintext-secret"
    secret_hash = hash_client_secret(plaintext)

    assert verify_client_secret(plaintext, secret_hash)


def test_verify_client_secret_rejects_wrong_secret():
    secret_hash = hash_client_secret("sf_live_correct-secret")

    assert not verify_client_secret("sf_live_wrong-secret", secret_hash)


def test_verify_client_secret_rejects_malformed_hash():
    assert not verify_client_secret("sf_live_secret", "not-a-valid-hash")


def test_verify_client_secret_rejects_invalid_hash_parts():
    assert not verify_client_secret("sf_live_secret", "$argon2id$not-valid")


@pytest.mark.parametrize("nbytes", [0, -1])
def test_generate_client_secret_rejects_invalid_entropy_length(nbytes):
    with pytest.raises(ValueError, match="nbytes must be positive"):
        generate_client_secret(nbytes=nbytes)
