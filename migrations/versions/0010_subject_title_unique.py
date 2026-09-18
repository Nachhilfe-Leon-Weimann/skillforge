"""make core.subject.title unique, case-insensitively

Revision ID: 0010_subject_title_unique
Revises: 0009_cancel_operation
Create Date: 2026-09-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_subject_title_unique"
down_revision: str | None = "0009_cancel_operation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fails if two existing titles differ only in case; merge them by hand first (CRM API spec).
    op.create_index(
        "uq_subject_title_lower",
        "subject",
        [sa.literal_column("lower(title)")],
        unique=True,
        schema="core",
    )


def downgrade() -> None:
    op.drop_index("uq_subject_title_lower", table_name="subject", schema="core")
