"""``normalize_contact_value``: the one function every contact value passes, in the API and in the services."""

import unicodedata

import pytest

from app.core.db.models import ContactInfoType
from app.services.crm.inputs import MAX_CONTACT_VALUE_LENGTH, normalize_contact_value

EMAIL, PHONE = ContactInfoType.EMAIL, ContactInfoType.PHONE

# Letters whose lowercase form is longer, or is no longer NFC once lowercased: "J" + caron lowercases
# to "j" + caron, which NFC then composes to U+01F0.
TRICKY_LOCAL_PARTS = ["J̌x", "Ϊ̀x", "İx", "STRASSE", "ẞ", "ǅ", "Å"]


@pytest.mark.parametrize(
    ("type", "raw", "normalized"),
    [
        (EMAIL, "  Max.Mustermann@Example.COM ", "max.mustermann@example.com"),
        (EMAIL, "Max Mustermann <Max@Example.com>", "max@example.com"),
        (PHONE, " +49 151\t234 567\n", "+49151234567"),
        (PHONE, "0151/234-567", "0151/234-567"),
    ],
)
def test_normalize_contact_value(type: ContactInfoType, raw: str, normalized: str):
    assert normalize_contact_value(type, raw) == normalized


@pytest.mark.parametrize("local", TRICKY_LOCAL_PARTS)
def test_normalizing_an_email_twice_changes_nothing(local: str):
    """The create routes normalize in the request model and again in the service."""
    once = normalize_contact_value(EMAIL, f"{local}@example.com")

    assert normalize_contact_value(EMAIL, once) == once
    assert once == once.lower()
    assert unicodedata.is_normalized("NFC", once)


def test_normalizing_is_a_fixed_point_across_letters_and_combining_marks():
    marks = ["", "̀", "́", "̌", "̈"]
    letters = [chr(code) for code in (*range(0x41, 0x250), *range(0x370, 0x530), *range(0x1E00, 0x2000))]
    checked = 0
    for letter in letters:
        for mark in marks:
            try:
                once = normalize_contact_value(EMAIL, f"{letter}{mark}x@example.com")
            except ValueError:
                continue
            checked += 1
            assert normalize_contact_value(EMAIL, once) == once, (hex(ord(letter)), mark.encode("unicode_escape"))

    assert checked > 1000


def test_two_spellings_that_normalize_alike_are_equal_after_one_pass():
    assert normalize_contact_value(EMAIL, "J̌x@example.com") == normalize_contact_value(EMAIL, "ǰx@example.com")


def test_an_address_that_only_fits_before_lowercasing_is_rejected_on_the_first_pass():
    """U+0130 lowercases to two code points: the stored form would exceed what an address may be."""
    with pytest.raises(ValueError):
        normalize_contact_value(EMAIL, "İ" * 100 + "@example.com")


@pytest.mark.parametrize(
    ("type", "value"),
    [
        (EMAIL, "not-an-email"),
        (EMAIL, ""),
        (PHONE, " \t "),
        (PHONE, "1" * (MAX_CONTACT_VALUE_LENGTH + 1)),
        (PHONE, "1\x00"),
        (PHONE, "1\ud83d"),
    ],
    ids=["invalid e-mail", "empty e-mail", "blank phone", "oversized phone", "nul", "lone surrogate"],
)
def test_an_invalid_value_is_a_value_error_that_never_repeats_the_value(type: ContactInfoType, value: str):
    with pytest.raises(ValueError) as raised:
        normalize_contact_value(type, value)

    assert value.strip() == "" or value.strip() not in str(raised.value)
    assert raised.value.__cause__ is None


def test_a_phone_number_of_the_maximum_length_is_accepted():
    assert normalize_contact_value(PHONE, " " + "1" * MAX_CONTACT_VALUE_LENGTH) == "1" * MAX_CONTACT_VALUE_LENGTH
