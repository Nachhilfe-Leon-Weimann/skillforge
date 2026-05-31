import os
import shutil

import pytest

from app.core.db.models.schemata import get_schemata

FAKE_SCHEMA_DIR = "tests/db/models/schemata_test"


@pytest.mark.db
def test_get_schemata():
    fake_schemata = "primary", "secondary", "_not_relevant"
    # ensure base test dir exists
    os.makedirs(FAKE_SCHEMA_DIR, exist_ok=True)

    for fake_schema in fake_schemata:
        _create_fake_schema(fake_schema)

    try:
        schemata = get_schemata(FAKE_SCHEMA_DIR)
        assert "primary" in schemata
        assert "secondary" in schemata
        assert "_not_relevant" not in schemata
    finally:
        for fake_schema in fake_schemata:
            _delete_fake_schema(fake_schema)
        # remove base dir if empty
        try:
            os.rmdir(FAKE_SCHEMA_DIR)
        except OSError:
            pass


def _create_fake_schema(schema_name: str):
    os.makedirs(f"{FAKE_SCHEMA_DIR}/{schema_name}", exist_ok=True)


def _delete_fake_schema(schema_name: str):
    path = f"{FAKE_SCHEMA_DIR}/{schema_name}"
    if os.path.exists(path):
        shutil.rmtree(path)
