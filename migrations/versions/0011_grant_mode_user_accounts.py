"""add grant modes and the user account tables

Revision ID: 0011_grant_mode_user_accounts
Revises: 0010_subject_title_unique
Create Date: 2026-09-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_grant_mode_user_accounts"
down_revision: str | None = "0010_subject_title_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.add_column does not create the enum type of the column it adds (op.create_table below
    # does, for each new table's own type), so create grant_mode first. The server default turns
    # every existing grant into an `application` grant: each client keeps exactly its rights.
    op.execute(sa.text("CREATE TYPE public.grant_mode AS ENUM ('application', 'delegated')"))
    op.add_column(
        "application_client_scope_grant",
        sa.Column(
            "mode",
            sa.Enum("application", "delegated", name="grant_mode"),
            server_default=sa.text("'application'"),
            nullable=False,
        ),
        schema="auth",
    )
    # Autogenerate does not detect a primary-key change (and `alembic check` is blind to it): widen
    # the key by hand, under the same name, so that one scope can be granted in both modes.
    op.drop_constraint(
        "application_client_scope_grant_pkey", "application_client_scope_grant", schema="auth", type_="primary"
    )
    op.create_primary_key(
        "application_client_scope_grant_pkey",
        "application_client_scope_grant",
        ["application_client_id", "scope_key", "mode"],
        schema="auth",
    )

    op.create_table(
        "user_account",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("party_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", name="user_account_status"),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("email = lower(email)", name="ck_user_account_email_lowercase"),
        sa.ForeignKeyConstraint(["party_id"], ["core.party.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("party_id"),
        schema="auth",
    )
    op.create_table(
        "user_account_role",
        sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Enum("admin", name="user_account_role_name"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_account_id"], ["auth.user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_account_id", "role"),
        schema="auth",
    )
    op.create_table(
        "user_action_token",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.Enum("invitation", "password_reset", name="user_action_token_purpose"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_account_id"], ["auth.user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema="auth",
    )
    op.create_index(
        "ix_user_action_token_user_account_id", "user_action_token", ["user_account_id"], unique=False, schema="auth"
    )
    op.create_table(
        "user_session",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_account_id", sa.UUID(), nullable=False),
        sa.Column("application_client_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False),
        sa.Column("previous_refresh_token_hash", sa.String(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["application_client_id"], ["auth.application_client.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_account_id"], ["auth.user_account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("previous_refresh_token_hash"),
        sa.UniqueConstraint("refresh_token_hash"),
        schema="auth",
    )
    op.create_index(
        "ix_user_session_application_client_id",
        "user_session",
        ["application_client_id"],
        unique=False,
        schema="auth",
    )
    op.create_index("ix_user_session_user_account_id", "user_session", ["user_account_id"], unique=False, schema="auth")


def downgrade() -> None:
    op.drop_index("ix_user_session_user_account_id", table_name="user_session", schema="auth")
    op.drop_index("ix_user_session_application_client_id", table_name="user_session", schema="auth")
    op.drop_table("user_session", schema="auth")
    op.drop_index("ix_user_action_token_user_account_id", table_name="user_action_token", schema="auth")
    op.drop_table("user_action_token", schema="auth")
    op.drop_table("user_account_role", schema="auth")
    op.drop_table("user_account", schema="auth")

    # The two-column key cannot express a ceiling: a `delegated` grant kept without its mode would
    # become the client's own right on its next client_credentials token (decision F of the
    # user-authentication spec). Delete those grants before the key narrows - a lost ceiling costs
    # a re-grant, a promoted one is a leak. What remains is unique per (client, scope) already.
    op.execute(sa.text("DELETE FROM auth.application_client_scope_grant WHERE mode = 'delegated'"))
    op.drop_constraint(
        "application_client_scope_grant_pkey", "application_client_scope_grant", schema="auth", type_="primary"
    )
    op.create_primary_key(
        "application_client_scope_grant_pkey",
        "application_client_scope_grant",
        ["application_client_id", "scope_key"],
        schema="auth",
    )
    op.drop_column("application_client_scope_grant", "mode", schema="auth")

    # Dropping a table or a column leaves its enum type behind, so drop all four explicitly, as
    # the baseline does for every type it introduces.
    op.execute(sa.text("DROP TYPE IF EXISTS public.grant_mode"))
    op.execute(sa.text("DROP TYPE IF EXISTS public.user_account_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS public.user_account_role_name"))
    op.execute(sa.text("DROP TYPE IF EXISTS public.user_action_token_purpose"))
