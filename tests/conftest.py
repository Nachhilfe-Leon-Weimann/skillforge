import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "session" in item.fixturenames:
            if not item.get_closest_marker("db"):
                raise pytest.UsageError(f"{item.name} uses DB but is missing @pytest.mark.db")
