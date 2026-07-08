"""add off-boarding operation kinds (student_deactivate, tutor_deactivate)

Revision ID: 0007_off_boarding_transitions
Revises: 0006_worker_heartbeat
Create Date: 2026-07-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_off_boarding_transitions"
down_revision: str | None = "0006_worker_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction as long as the new
    # value is not *used* in the same transaction (this migration only adds labels, never
    # inserts rows with them, so it is safe).
    op.execute(sa.text("ALTER TYPE bot.operation_kind ADD VALUE IF NOT EXISTS 'student_deactivate'"))
    op.execute(sa.text("ALTER TYPE bot.operation_kind ADD VALUE IF NOT EXISTS 'tutor_deactivate'"))


def downgrade() -> None:
    # Postgres cannot drop an enum label in place, so recreate the type without the
    # off-boarding kinds: rename the old type aside, create the original 4-value type,
    # re-cast the column, then drop the old type. The re-cast fails if any operation row
    # still uses a removed label - an accepted limitation of downgrading past this data.
    op.execute(sa.text("ALTER TYPE bot.operation_kind RENAME TO operation_kind_old"))
    op.execute(
        sa.text(
            "CREATE TYPE bot.operation_kind AS ENUM "
            "('tutor_activate', 'student_activate', 'student_stash', 'student_pop')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE bot.operation ALTER COLUMN kind TYPE bot.operation_kind USING kind::text::bot.operation_kind"
        )
    )
    op.execute(sa.text("DROP TYPE bot.operation_kind_old"))
