"""enforce one open (PREPARED) operation per (guild, subject, kind) via a partial unique index

Revision ID: 0008_idempotent_prepare
Revises: 0007_off_boarding_transitions
Create Date: 2026-07-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_idempotent_prepare"
down_revision: str | None = "0007_off_boarding_transitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Make `prepare` idempotent at the DB layer: at most one open (PREPARED) reservation per
    # (guild, subject, kind). The predicate can only be `status='prepared'` - `now()` is not
    # IMMUTABLE, so `expires_at` cannot go in the index; an expired-but-unswept row still holds the
    # slot and is reclaimed in-app on collision.
    #
    # The double-booking bug this fixes may already have left duplicate open reservations in place,
    # which would fail the unique-index build. Collapse them first: keep the one with the latest
    # expiry (tie-broken by operation_id) and materialize the rest to EXPIRED - the same transition
    # the sweeper/app reclaim applies. This is idempotent (a no-op when no duplicates exist).
    op.execute(
        sa.text(
            """
            UPDATE bot.operation AS o
            SET status = 'expired'
            WHERE o.status = 'prepared'
              AND EXISTS (
                  SELECT 1
                  FROM bot.operation AS other
                  WHERE other.status = 'prepared'
                    AND other.guild_id = o.guild_id
                    AND other.subject_discord_id = o.subject_discord_id
                    AND other.kind = o.kind
                    AND (other.expires_at, other.operation_id) > (o.expires_at, o.operation_id)
              )
            """
        )
    )
    op.create_index(
        "uq_operation_prepared_subject_kind",
        "operation",
        ["guild_id", "subject_discord_id", "kind"],
        unique=True,
        schema="bot",
        postgresql_where=sa.text("status = 'prepared'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_operation_prepared_subject_kind",
        table_name="operation",
        schema="bot",
        postgresql_where=sa.text("status = 'prepared'"),
    )
