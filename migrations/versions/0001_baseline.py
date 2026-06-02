"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-02

Initializes Alembic on the existing model-first schema. The schema is created directly from
the SQLAlchemy metadata (schemas + create_all) rather than transcribed into granular ops.
This guarantees the baseline is identical to what the application defines (and to the test
suite's create_all) and avoids autogenerate pitfalls with Postgres enums shared across tables
(e.g. ``bot.member_role`` used by both ``discord_user`` and ``discord_role_binding``).

Subsequent migrations are authored normally (autogenerate diffs models against this baseline).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db.models  # noqa: F401  -- populates Base.metadata
from app.core.db.models.base import Base
from app.core.db.models.schemata import get_schemata

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for schema in sorted(get_schemata()):
        op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    for schema in sorted(get_schemata()):
        op.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
