import pytest

from app.core.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, dummy_password_hash, meets_password_policy
from app.core.auth.secrets import verify_secret


def test_the_policy_is_8_to_128_characters():
    assert (MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH) == (8, 128)


@pytest.mark.parametrize(("length", "accepted"), [(0, False), (7, False), (8, True), (128, True), (129, False)])
def test_only_the_length_counts(length: int, accepted: bool):
    assert meets_password_policy("a" * length) is accepted


@pytest.mark.parametrize("password", ["aaaaaaaa", "12345678", "        ", "passwort"])
def test_no_composition_rules_and_no_list_of_forbidden_passwords(password: str):
    assert meets_password_policy(password)


def test_the_dummy_hash_is_a_real_hash_computed_once():
    assert dummy_password_hash() is dummy_password_hash()
    assert dummy_password_hash().startswith("$argon2")
    assert not verify_secret("", dummy_password_hash())


def test_a_password_that_is_no_text_violates_the_policy():
    """A lone surrogate is valid JSON but no UTF-8: it could not be hashed."""
    assert not meets_password_policy("a long password \ud800")
