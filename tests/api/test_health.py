from fastapi.testclient import TestClient

from app.core.db.dependencies import get_database
from app.main import app


def test_health_returns_ok_when_database_is_healthy():
    fake_database = _FakeDatabase(healthy=True)

    with _database_override(fake_database):
        response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}
    assert fake_database.disposed is True


def test_health_returns_unavailable_when_database_is_down():
    fake_database = _FakeDatabase(healthy=False)

    with _database_override(fake_database):
        response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "checks": {"database": "down"}}
    assert fake_database.disposed is True


class _FakeDatabase:
    def __init__(self, *, healthy: bool):
        self.healthy = healthy
        self.disposed = False

    async def health(self) -> bool:
        return self.healthy

    async def dispose(self) -> None:
        self.disposed = True


class _database_override:
    def __init__(self, database: _FakeDatabase):
        self.database = database

    def __enter__(self):
        app.dependency_overrides[get_database] = lambda: self.database
        return self

    def __exit__(self, exc_type, exc, tb):
        app.dependency_overrides.clear()
