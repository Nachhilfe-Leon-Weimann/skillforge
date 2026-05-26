import os

import pytest

os.environ.setdefault("AUTH__SECRET_KEY", "test-signing-secret-with-at-least-32-bytes")
os.environ.setdefault("DB__URL", "postgresql+asyncpg://skillforge:skillforge@localhost:5432/skillforge")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "session" in item.fixturenames:
            if not item.get_closest_marker("db"):
                raise pytest.UsageError(f"{item.name} uses DB but is missing @pytest.mark.db")
