"""Create users and sessions tables.

The migration is the durable PostgreSQL representation of the user-management
contract.  It includes database-level checks and indexes so invariants remain
true even if a future tool bypasses the FastAPI service layer.

Revision ID: 20261022_0001
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261022_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the extension, profile table, session table, and supporting indexes."""

    # ``pgcrypto`` provides PostgreSQL-side UUID generation for inserts that do
    # not originate in the SQLAlchemy ORM.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("college", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column(
            "hobbies",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "custom_hobbies",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        # These constraints mirror the generated API schema and service rules.
        sa.CheckConstraint("birth_date >= DATE '1960-01-01'", name="birth_date_min"),
        sa.CheckConstraint("birth_date <= DATE '2000-01-01'", name="birth_date_max"),
        sa.CheckConstraint("role IN ('admin', 'user')", name="role_valid"),
        sa.CheckConstraint("gender IN ('Men', 'Women')", name="gender_valid"),
        sa.CheckConstraint("length(trim(name)) > 0", name="name_non_empty"),
        sa.CheckConstraint("cardinality(hobbies) <= 20", name="hobbies_max_items"),
        sa.CheckConstraint("cardinality(custom_hobbies) <= 20", name="custom_hobbies_max_items"),
        sa.CheckConstraint("array_position(hobbies, '') IS NULL", name="hobbies_no_empty"),
        sa.CheckConstraint(
            "array_position(custom_hobbies, '') IS NULL", name="custom_hobbies_no_empty"
        ),
        sa.CheckConstraint(
            "(is_active = true AND deleted_at IS NULL) OR "
            "(is_active = false AND deleted_at IS NOT NULL)",
            name="active_deleted_consistent",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Indexes support the active-user list path and its case-insensitive email
    # uniqueness rule without scanning deleted history.
    op.create_index("ix_users_active_name", "users", ["is_active", "name"], unique=False)
    op.create_index(
        "ix_users_active_created_at", "users", ["is_active", "created_at"], unique=False
    )
    op.create_index(
        "uq_users_active_email",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("is_active = true AND deleted_at IS NULL"),
    )

    # Session records deliberately contain hashes, expiry, and revocation
    # timestamps rather than raw cookie/CSRF values.
    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_user_sessions_user_expires", "user_sessions", ["user_id", "expires_at"], unique=False
    )


def downgrade() -> None:
    """Remove the session and user schema in reverse dependency order."""

    op.drop_index("ix_user_sessions_user_expires", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("uq_users_active_email", table_name="users")
    op.drop_index("ix_users_active_created_at", table_name="users")
    op.drop_index("ix_users_active_name", table_name="users")
    op.drop_table("users")
