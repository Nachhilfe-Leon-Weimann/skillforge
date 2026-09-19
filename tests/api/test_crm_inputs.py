"""``normalize_contact_value``: the one function every contact value passes, in the API and in the services."""

import re
import unicodedata
from pathlib import Path

import pytest

from app.core.db.models import ContactInfoType
from app.services.crm.inputs import DEFAULT_PHONE_REGION, MAX_CONTACT_VALUE_LENGTH, normalize_contact_value

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
        (PHONE, "0151/234-567", "+49151234567"),
    ],
)
def test_normalize_contact_value(type: ContactInfoType, raw: str, normalized: str):
    assert normalize_contact_value(type, raw) == normalized


# One mobile number the way people write it down; a national form is read with the default region.
SPELLINGS_OF_ONE_NUMBER = [
    "0171 1234567",
    "+49 171 1234567",
    "0049 171 1234567",
    "+49 (0)171 1234567",
    "0171/1234567",
    "0171-123 45 67",
    "+491711234567",
]


@pytest.mark.parametrize("raw", SPELLINGS_OF_ONE_NUMBER)
def test_every_spelling_of_a_phone_number_is_stored_as_the_same_e164_form(raw: str):
    assert normalize_contact_value(PHONE, raw) == "+491711234567"


def test_a_national_number_is_read_with_the_default_region():
    assert DEFAULT_PHONE_REGION == "DE"
    assert normalize_contact_value(PHONE, "030 1234567") == "+49301234567"


def test_a_foreign_number_keeps_its_country():
    assert normalize_contact_value(PHONE, "+1 650 253 0000") == "+16502530000"


@pytest.mark.parametrize("raw", [*SPELLINGS_OF_ONE_NUMBER, "030 1234567", "+1 650 253 0000", "0151/234-567"])
def test_normalizing_a_phone_number_twice_changes_nothing(raw: str):
    once = normalize_contact_value(PHONE, raw)

    assert normalize_contact_value(PHONE, once) == once


@pytest.mark.parametrize(
    "value",
    [
        "0800 FLOWERS",
        "+49171123456a",
        "0171 1234567 (Mama)",
        "+",
        "12",
        "112",
        "+49 171 12345678901234",
        "0171 1234567 und 0172 7654321",
    ],
    ids=[
        "vanity letters",
        "trailing letter",
        "a note",
        "no digits",
        "too short",
        "local only",
        "too long",
        "two numbers",
    ],
)
def test_what_has_no_e164_form_is_not_a_phone_number(value: str):
    with pytest.raises(ValueError, match="^Value is not a valid phone number$"):
        normalize_contact_value(PHONE, value)


@pytest.mark.parametrize(
    "value", ["0171 1234567 ext. 12", "0171 1234567x5", "0171 1234567 #5", "+49 171 1234567;ext=5"]
)
def test_an_extension_is_rejected_rather_than_dropped(value: str):
    """E.164 has no extension, and formatting would lose it without a word."""
    with pytest.raises(ValueError, match="extension") as raised:
        normalize_contact_value(PHONE, value)

    assert "label" in str(raised.value)


def test_only_the_inputs_module_knows_the_phone_library():
    app_dir = Path(__file__).parents[2] / "app"
    importers = {
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if re.search(r"^\s*(import|from) phonenumbers\b", path.read_text(), flags=re.MULTILINE)
    }

    assert importers == {"services/crm/inputs.py"}


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
        (PHONE, "0171 9876543 (Mama)"),
        (PHONE, "0171 9876543 ext. 12"),
        (PHONE, "1\x00"),
        (PHONE, "1\ud83d"),
    ],
    ids=[
        "invalid e-mail",
        "empty e-mail",
        "blank phone",
        "oversized phone",
        "phone with a note",
        "phone with an extension",
        "nul",
        "lone surrogate",
    ],
)
def test_an_invalid_value_is_a_value_error_that_never_repeats_the_value(type: ContactInfoType, value: str):
    with pytest.raises(ValueError) as raised:
        normalize_contact_value(type, value)

    assert value.strip() == "" or value.strip() not in str(raised.value)
    assert raised.value.__cause__ is None
