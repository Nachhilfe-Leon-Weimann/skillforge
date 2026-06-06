from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock

from app.core.db import Database
from app.workers import reaper as reaper_worker


class _StubLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.exceptions: list[tuple[str, dict]] = []

    def info(self, event, **fields):
        self.calls.append((event, fields))

    def exception(self, event, **fields):
        self.exceptions.append((event, fields))


class _StubDatabase:
    """Stands in for ``Database`` so ``run_cycle`` runs without a real connection."""

    @asynccontextmanager
    async def session(self, *, write: bool = True):
        yield None


async def test_run_cycle_logs_single_counter_line(monkeypatch):
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", AsyncMock(return_value=(2, 1)))
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(return_value=3))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    assert len(logger.calls) == 1
    event, fields = logger.calls[0]
    assert event == "reaper_cycle"
    assert fields["jobs_reclaimed"] == 2
    assert fields["jobs_dead_lettered"] == 1
    assert fields["operations_expired"] == 3
    assert "duration_ms" in fields


async def test_run_cycle_logs_zero_values_when_idle(monkeypatch):
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(return_value=0))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    # No hits still produces exactly one line with all four fields at zero - no silent silence.
    assert len(logger.calls) == 1
    _, fields = logger.calls[0]
    assert fields["jobs_reclaimed"] == 0
    assert fields["jobs_dead_lettered"] == 0
    assert fields["operations_expired"] == 0


async def test_run_cycle_still_logs_counter_when_reap_fails(monkeypatch):
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(return_value=4))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    # A failed reap (rolled back) leaves its counters at zero but must not swallow the line.
    assert len(logger.calls) == 1
    event, fields = logger.calls[0]
    assert event == "reaper_cycle"
    assert fields["jobs_reclaimed"] == 0
    assert fields["jobs_dead_lettered"] == 0
    assert fields["operations_expired"] == 4
    assert any(name == "reaper_reap_failed" for name, _ in logger.exceptions)


async def test_run_cycle_still_logs_counter_when_sweep_fails(monkeypatch):
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", AsyncMock(return_value=(2, 1)))
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(side_effect=RuntimeError("db down")))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    # The reap counts survive a later sweep failure; the line is still emitted exactly once.
    assert len(logger.calls) == 1
    event, fields = logger.calls[0]
    assert event == "reaper_cycle"
    assert fields["jobs_reclaimed"] == 2
    assert fields["jobs_dead_lettered"] == 1
    assert fields["operations_expired"] == 0
    assert any(name == "reaper_sweep_failed" for name, _ in logger.exceptions)


async def test_run_cycle_drains_backlog_across_batches(monkeypatch):
    monkeypatch.setattr(reaper_worker, "REAP_BATCH_LIMIT", 2)
    # Two full batches (limit reached -> keep draining) then a short one (backlog empty -> stop).
    reap = AsyncMock(side_effect=[(2, 0), (1, 1), (1, 0)])
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", reap)
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(return_value=0))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    # Stops after the first short batch; counts are summed across all batches, one line emitted.
    assert reap.await_count == 3
    assert all(kwargs == {"batch_limit": 2} for _, kwargs in reap.await_args_list)
    assert len(logger.calls) == 1
    _, fields = logger.calls[0]
    assert fields["jobs_reclaimed"] == 4
    assert fields["jobs_dead_lettered"] == 1


async def test_run_cycle_keeps_committed_batch_counts_when_a_later_batch_fails(monkeypatch):
    monkeypatch.setattr(reaper_worker, "REAP_BATCH_LIMIT", 2)
    # First batch commits (counts kept), second batch's transaction fails and rolls back itself.
    reap = AsyncMock(side_effect=[(2, 0), RuntimeError("db down")])
    monkeypatch.setattr(reaper_worker, "reap_expired_jobs", reap)
    monkeypatch.setattr(reaper_worker, "sweep_expired_operations", AsyncMock(return_value=3))
    logger = _StubLogger()

    await reaper_worker.run_cycle(cast(Database, _StubDatabase()), logger)

    assert len(logger.calls) == 1
    _, fields = logger.calls[0]
    assert fields["jobs_reclaimed"] == 2  # the committed batch survives the later failure
    assert fields["jobs_dead_lettered"] == 0
    assert fields["operations_expired"] == 3
    assert any(name == "reaper_reap_failed" for name, _ in logger.exceptions)
