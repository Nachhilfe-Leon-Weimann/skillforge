"""The password policy and the hashing behind it live in one module (spec: security rules)."""

import importlib

import pytest

from app.core.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    dummy_password_hash,
    hash_password,
    meets_password_policy,
    verify_password,
)


@pytest.mark.parametrize("length", [0, 1, MIN_PASSWORD_LENGTH - 1, MAX_PASSWORD_LENGTH + 1])
def test_a_password_outside_the_length_range_is_refused(length: int):
    assert not meets_password_policy("x" * length)


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH])
def test_a_password_inside_the_length_range_is_accepted(length: int):
    assert meets_password_policy("x" * length)


def test_the_policy_has_no_composition_rules():
    assert meets_password_policy("            ")


def test_a_hash_verifies_the_password_it_was_made_from():
    password = "correct horse battery staple"

    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password(password + "!", password_hash)


def test_hashing_the_same_password_twice_gives_different_hashes():
    """Argon2 salts every hash, so a stolen table shows no two accounts sharing a password."""
    assert hash_password("correct horse battery") != hash_password("correct horse battery")


def test_a_hash_of_an_unknown_format_is_a_mismatch_rather_than_a_crash():
    assert not verify_password("correct horse battery", "not-a-hash")


def test_the_dummy_hash_is_a_real_hash_that_no_password_matches():
    """It is verified against when no account matched, so the two paths cost the same time."""
    assert dummy_password_hash().startswith("$argon2")
    assert not verify_password("correct horse battery staple", dummy_password_hash())


def test_the_dummy_hash_is_computed_on_first_use_and_then_kept():
    """Importing the module must not cost an Argon2 hash - every process pays that one."""
    assert dummy_password_hash() is dummy_password_hash()
    assert dummy_password_hash.cache_info().currsize == 1


def test_importing_the_module_hashes_nothing():
    module = importlib.reload(importlib.import_module("app.core.auth.passwords"))

    assert module.dummy_password_hash.cache_info().currsize == 0
