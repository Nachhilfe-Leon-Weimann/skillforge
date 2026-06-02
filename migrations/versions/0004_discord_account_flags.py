"""add ext.discord_account.is_primary and active

Revision ID: 0004_discord_account_flags
Revises: 0003_job
Create Date: 2026-06-03

The pre-Alembic production schema for ext.discord_account was missing the is_primary and
active columns (added later in the model). This migration adds them, drops the old blanket
UNIQUE(party_id) constraint, and creates the new targeted indexes:
- ix_discord_account_party_id_active (non-unique)
- uq_discord_account_party_id_primary_active (unique WHERE is_primary AND active)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_discord_account_flags"
down_revision: str | None = "0003_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discord_account",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="ext",
    )
    op.add_column(
        "discord_account",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        schema="ext",
    )
    # Prod DB had a blanket UNIQUE(party_id) constraint; fresh DBs (built from the baseline)
    # do not. Use IF EXISTS so this migration is safe in both paths.
    op.execute(sa.text('ALTER TABLE ext.discord_account DROP CONSTRAINT IF EXISTS "discord_account_party_id_key"'))
    op.create_index(
        "ix_discord_account_party_id_active",
        "discord_account",
        ["party_id", "active"],
        unique=False,
        schema="ext",
    )
    op.create_index(
        "uq_discord_account_party_id_primary_active",
        "discord_account",
        ["party_id"],
        unique=True,
        schema="ext",
        postgresql_where=sa.text("is_primary IS true AND active IS true"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_discord_account_party_id_primary_active",
        table_name="discord_account",
        schema="ext",
        postgresql_where=sa.text("is_primary IS true AND active IS true"),
    )
    op.drop_index("ix_discord_account_party_id_active", table_name="discord_account", schema="ext")
    op.create_unique_constraint("discord_account_party_id_key", "discord_account", ["party_id"], schema="ext")
    op.drop_column("discord_account", "active", schema="ext")
    op.drop_column("discord_account", "is_primary", schema="ext")
