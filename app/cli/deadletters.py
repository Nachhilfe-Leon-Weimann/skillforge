"""Operator CLI for the job dead-letter queue.

Two one-shot commands for inspecting and recovering ``FAILED`` jobs, exposed via ``just``
(``dead-jobs`` / ``requeue``) and run as ``python -m app.cli.deadletters <command>``:

- ``list`` -- show all dead-lettered jobs with ``kind``, ``last_error`` and ``failed_at``.
- ``requeue <job_id>`` -- reset a dead-lettered job to ``PENDING`` so it is claimable again.

Both use the pooled app DB connection. Scope: lifecycle guardian spec, P1.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import Database
from app.services.bot import (
    BotServiceError,
    list_dead_lettered_jobs,
    requeue_job,
)


async def _with_session[T](action: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Open a single pooled session, run ``action``, and always dispose the engine."""
    database = Database.from_url(str(get_settings().db.url))
    try:
        async with database.session() as session:
            return await action(session)
    finally:
        await database.dispose()


async def _list() -> None:
    jobs = await _with_session(list_dead_lettered_jobs)
    if not jobs:
        print("No dead-lettered jobs.")
        return

    print(f"{len(jobs)} dead-lettered job(s):")
    for job in jobs:
        failed_at = job.failed_at.isoformat() if job.failed_at else "-"
        print(f"  {job.job_id}  kind={job.kind}  failed_at={failed_at}  last_error={job.last_error or '-'}")


async def _requeue(job_id: uuid.UUID) -> None:
    job = await _with_session(lambda session: requeue_job(session, job_id=job_id))
    print(
        f"Requeued {job.job_id}: status={job.status.value}, attempt={job.attempt}, "
        f"available_at={job.available_at.isoformat()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="deadletters", description="Inspect and recover dead-lettered jobs.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list", help="List dead-lettered (FAILED) jobs.")
    requeue_parser = subcommands.add_parser("requeue", help="Requeue a dead-lettered job by id.")
    requeue_parser.add_argument("job_id", type=uuid.UUID, help="The FAILED job's id.")

    args = parser.parse_args()

    try:
        if args.command == "requeue":
            asyncio.run(_requeue(args.job_id))
        else:
            asyncio.run(_list())
    except BotServiceError as exc:
        # Unknown id or a non-FAILED job: a clear operator-facing message, no traceback.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
