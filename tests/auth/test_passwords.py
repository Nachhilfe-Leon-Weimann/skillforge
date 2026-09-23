"""The password policy lives in one module (spec: security rules); hashing is ``secrets.py``'s."""

import importlib

import pytest

from app.core.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    dummy_password_hash,
    meets_password_policy,
)
from app.core.auth.secrets import verify_secret


@pytest.mark.parametrize("length", [0, 1, MIN_PASSWORD_LENGTH - 1, MAX_PASSWORD_LENGTH + 1])
def test_a_password_outside_the_length_range_is_refused(length: int):
    assert not meets_password_policy("x" * length)


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH])
def test_a_password_inside_the_length_range_is_accepted(length: int):
    assert meets_password_policy("x" * length)


def test_the_policy_has_no_composition_rules():
    assert meets_password_policy("            ")


def test_the_dummy_hash_is_a_real_hash_that_no_password_matches():
    """It is verified against when no account matched, so the two paths cost the same time."""
    assert dummy_password_hash().startswith("$argon2")
    assert not verify_secret("correct horse battery staple", dummy_password_hash())


def test_the_dummy_hash_is_computed_on_first_use_and_then_kept():
    """Importing the module must not cost an Argon2 hash - every process pays that one."""
    assert dummy_password_hash() is dummy_password_hash()
    assert dummy_password_hash.cache_info().currsize == 1


def test_importing_the_module_hashes_nothing():
    module = importlib.reload(importlib.import_module("app.core.auth.passwords"))

    assert module.dummy_password_hash.cache_info().currsize == 0
