"""convert ext.discord_account.discord_id to BIGINT

Revision ID: 0002_discord_id_bigint
Revises: 0001_baseline
Create Date: 2026-06-02

Discord snowflakes are 64-bit integers. The column historically existed as VARCHAR in the
pre-Alembic production database; the baseline reflects that, and this revision converts it to
BIGINT so it matches the model and lines up with bot.discord_user.discord_id (the value-join
behind StudentContext.party_id). Existing string values are numeric snowflakes and cast cleanly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_discord_id_bigint"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "discord_account",
        "discord_id",
        schema="ext",
        existing_type=sa.String(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        postgresql_using="discord_id::bigint",
    )


def downgrade() -> None:
    op.alter_column(
        "discord_account",
        "discord_id",
        schema="ext",
        existing_type=sa.BigInteger(),
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="discord_id::text",
    )
