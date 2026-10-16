"""Initial migration metadata schema.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

from alembic import op
from migrateiq import models  # noqa: F401
from migrateiq.db import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The model metadata is the single source of truth for this initial schema.
    # Subsequent changes use ordinary granular Alembic operations.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
