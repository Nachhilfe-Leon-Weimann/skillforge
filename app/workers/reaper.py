"""Dedicated worker that runs the lifecycle guardian on a loop.

Started as its own service in ``compose.yml`` (same image as the app, separate entrypoint,
pooled DB connection) via ``python -m app.workers.reaper``. Each cycle reaps expired job
leases and sweeps stale prepared operations, then emits one structured counter line. The
reclaim/sweep queries are concurrency-safe (``SKIP LOCKED`` / idempotent bulk update), so
running multiple replicas causes no double effects.
"""

import asyncio
import signal
import time
from datetime import timedelta

from app.core.config import get_settings
from app.core.db import Database
from app.core.db.models import WorkerCycleStatus
from app.core.logging import configure_logging, get_logger
from app.services.bot.reaper import reap_expired_jobs, sweep_expired_operations
from app.services.system import WorkerName, record_worker_heartbeat

# How often the guardian runs a reap+sweep cycle.
REAPER_INTERVAL = timedelta(seconds=30)

# How long a heartbeat stays "fresh" for the health plane: a few cycles, so a single slow or
# missed tick doesn't flip the worker to unhealthy. Derived from REAPER_INTERVAL so changing
# the cadence can never desync the staleness threshold (the beat carries this window itself).
HEARTBEAT_FRESH_FOR = REAPER_INTERVAL * 3

# Max jobs a single reap transaction may lock and process. Bounds the lock-hold time and
# transaction size on a large backlog; one cycle keeps draining batches until the backlog is
# empty (see ``run_cycle``), so the bound never leaves expired jobs behind for the next tick.
REAP_BATCH_LIMIT = 100


async def run_cycle(database: Database, logger) -> None:
    """Run one reap+sweep cycle and emit exactly one structured counter line.

    Reaper and sweeper run in separate transactions so a failure in one never rolls back the
    other. The reaper drains the backlog in bounded batches (:data:`REAP_BATCH_LIMIT` per
    transaction) so a large backlog never locks an unbounded number of rows at once; committed
    batches keep their counts, and a batch that fails rolls back only itself. A failing pass is
    logged on its own. Either way the cycle always emits exactly one ``reaper_cycle`` line: no
    silent silence, so a systematic problem (e.g. the bot never committing, or the DB being
    unreachable) stays visible in every run.
    """
    started = time.perf_counter()

    jobs_reclaimed = 0
    jobs_dead_lettered = 0
    operations_expired = 0
    cycle_ok = True

    try:
        while True:
            async with database.session() as session:
                reclaimed, dead_lettered = await reap_expired_jobs(session, batch_limit=REAP_BATCH_LIMIT)
            jobs_reclaimed += reclaimed
            jobs_dead_lettered += dead_lettered
            # A short batch means the backlog is drained. Each reaped job leaves CLAIMED (to
            # PENDING with a future available_at, or to FAILED), so the next batch can't re-match
            # it -- the loop always makes progress and terminates.
            if reclaimed + dead_lettered < REAP_BATCH_LIMIT:
                break
    except Exception:
        logger.exception("reaper_reap_failed")
        cycle_ok = False

    try:
        async with database.session() as session:
            operations_expired = await sweep_expired_operations(session)
    except Exception:
        logger.exception("reaper_sweep_failed")
        cycle_ok = False

    duration_ms = round((time.perf_counter() - started) * 1000, 2)

    logger.info(
        "reaper_cycle",
        jobs_reclaimed=jobs_reclaimed,
        jobs_dead_lettered=jobs_dead_lettered,
        operations_expired=operations_expired,
        duration_ms=duration_ms,
    )

    # Liveness signal for the health plane: a separate transaction so a heartbeat write
    # never masks reap/sweep results -- and a missing beat is exactly what marks the worker
    # unhealthy. OK only when both reap and sweep ran cleanly, else DEGRADED (alive, failing).
    try:
        async with database.session() as session:
            await record_worker_heartbeat(
                session,
                worker_name=WorkerName.REAPER.value,
                status=WorkerCycleStatus.OK if cycle_ok else WorkerCycleStatus.DEGRADED,
                fresh_for=HEARTBEAT_FRESH_FOR,
                detail={
                    "jobs_reclaimed": jobs_reclaimed,
                    "jobs_dead_lettered": jobs_dead_lettered,
                    "operations_expired": operations_expired,
                    "duration_ms": duration_ms,
                },
            )
    except Exception:
        logger.exception("reaper_heartbeat_failed")


async def run_forever() -> None:
    """Run reap+sweep cycles on :data:`REAPER_INTERVAL` until SIGINT/SIGTERM arrives."""
    logger = get_logger(__name__)
    database = Database.from_url(str(get_settings().db.url))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - signal handlers unavailable (e.g. Windows)
            pass

    logger.info("reaper_started", interval_s=REAPER_INTERVAL.total_seconds())
    try:
        while not stop.is_set():
            try:
                await run_cycle(database, logger)
            except Exception:
                # run_cycle already handles reap/sweep failures internally; this is a backstop
                # for anything unexpected (e.g. logging itself) so one bad tick never stops the
                # loop -- compose would otherwise just restart us into the same state.
                logger.exception("reaper_cycle_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=REAPER_INTERVAL.total_seconds())
            except TimeoutError:
                # Expected: the interval elapsed without a stop signal, so loop into the next
                # cycle. A real shutdown sets `stop` instead, which ends the while loop.
                pass
    finally:
        await database.dispose()
        logger.info("reaper_stopped")


def main() -> None:
    configure_logging(get_settings().logging)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
