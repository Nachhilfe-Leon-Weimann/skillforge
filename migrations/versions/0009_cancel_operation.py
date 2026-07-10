"""add operation cancel: cancelled status + cancelled_at

Revision ID: 0009_cancel_operation
Revises: 0008_idempotent_prepare
Create Date: 2026-07-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_cancel_operation"
down_revision: str | None = "0008_idempotent_prepare"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction as long as the new value
    # is not *used* in the same transaction (adding the nullable column below does not use it).
    op.execute(sa.text("ALTER TYPE bot.operation_status ADD VALUE IF NOT EXISTS 'cancelled'"))
    op.add_column(
        "operation",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        schema="bot",
    )


def downgrade() -> None:
    op.drop_column("operation", "cancelled_at", schema="bot")
    # Postgres cannot drop an enum label in place, so recreate operation_status without
    # 'cancelled' (rename-aside pattern from 0007). Two partial indexes are predicated on
    # `status = 'prepared'` (ix_operation_reservation from 0005, uq_operation_prepared_subject_kind
    # from 0008); rebuilding them during the column type-swap would compare the new type against the
    # renamed-aside old type ("operator does not exist"), so drop them first and recreate them after.
    # (The plain index ix_operation_status_expires_at has no enum-literal predicate and auto-rebuilds
    # fine, so it is left untouched.) The status column also carries a server_default - drop it before
    # the swap and restore it after. The re-cast fails if any row still uses 'cancelled' - an accepted
    # limitation of downgrading past this data.
    op.drop_index(
        "uq_operation_prepared_subject_kind",
        table_name="operation",
        schema="bot",
        postgresql_where=sa.text("status = 'prepared'"),
    )
    op.drop_index(
        "ix_operation_reservation",
        table_name="operation",
        schema="bot",
        postgresql_where=sa.text("status = 'prepared' AND reserved_archive_category_channel_id IS NOT NULL"),
    )
    op.execute(sa.text("ALTER TABLE bot.operation ALTER COLUMN status DROP DEFAULT"))
    op.execute(sa.text("ALTER TYPE bot.operation_status RENAME TO operation_status_old"))
    op.execute(sa.text("CREATE TYPE bot.operation_status AS ENUM ('prepared', 'committed', 'expired', 'failed')"))
    op.execute(
        sa.text(
            "ALTER TABLE bot.operation ALTER COLUMN status TYPE bot.operation_status "
            "USING status::text::bot.operation_status"
        )
    )
    op.execute(sa.text("ALTER TABLE bot.operation ALTER COLUMN status SET DEFAULT 'prepared'"))
    op.execute(sa.text("DROP TYPE bot.operation_status_old"))
    op.create_index(
        "ix_operation_reservation",
        "operation",
        ["guild_id", "reserved_archive_category_channel_id"],
        unique=False,
        schema="bot",
        postgresql_where=sa.text("status = 'prepared' AND reserved_archive_category_channel_id IS NOT NULL"),
    )
    op.create_index(
        "uq_operation_prepared_subject_kind",
        "operation",
        ["guild_id", "subject_discord_id", "kind"],
        unique=True,
        schema="bot",
        postgresql_where=sa.text("status = 'prepared'"),
    )
