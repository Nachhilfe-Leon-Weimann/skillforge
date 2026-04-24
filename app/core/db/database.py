from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True)
class Database:
    """Async database abstraction providing engine, session handling, and health checks."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_url(cls, url: str, *, echo: bool = False) -> Database:
        engine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
        )

        session_factory = async_sessionmaker(
            engine,
            expire_on_commit=False,
            autoflush=False,
        )

        return cls(engine=engine, session_factory=session_factory)

    @asynccontextmanager
    async def session(self, *, write: bool = True) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                if write:
                    async with session.begin():
                        yield session
                else:
                    yield session
            except Exception:
                await session.rollback()
                raise

    async def health(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except (SQLAlchemyError, OSError):
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
