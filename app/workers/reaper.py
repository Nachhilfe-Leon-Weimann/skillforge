"""Dedicated worker that runs the lifecycle guardian on a loop.

Started as its own service in ``compose.yml`` (same image as the app, separate entrypoint,
pooled DB connection) via ``python -m app.workers.reaper``. Each cycle reaps expired job
leases and sweeps stale prepared operations. The reclaim/sweep queries are concurrency-safe
(``SKIP LOCKED`` / idempotent bulk update), so running multiple replicas causes no double
effects.
"""

import asyncio
import signal
from datetime import timedelta

from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import configure_logging, get_logger
from app.services.bot.reaper import reap_expired_jobs, sweep_expired_operations

# How often the guardian runs a reap+sweep cycle.
REAPER_INTERVAL = timedelta(seconds=30)


async def run_cycle(database: Database, logger) -> None:
    """Run one reap+sweep cycle.

    Reaper and sweeper run in separate transactions so a failure in one never rolls back the
    other; each failure is logged on its own and never stops the cycle.
    """
    try:
        async with database.session() as session:
            await reap_expired_jobs(session)
    except Exception:
        logger.exception("reaper_reap_failed")

    try:
        async with database.session() as session:
            await sweep_expired_operations(session)
    except Exception:
        logger.exception("reaper_sweep_failed")


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
                # A single failed cycle must not stop the loop; compose would otherwise just
                # restart us into the same state. Log and try again next tick.
                logger.exception("reaper_cycle_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=REAPER_INTERVAL.total_seconds())
            except TimeoutError:
                pass
    finally:
        await database.dispose()
        logger.info("reaper_stopped")


def main() -> None:
    configure_logging(get_settings().logging)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
