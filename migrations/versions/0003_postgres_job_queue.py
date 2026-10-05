"""Add the PostgreSQL job queue used by the local production stack."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_postgres_job_queue"
down_revision: str | None = "0002_async_analysis_worker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the queue table and the index used to find ready messages."""
    op.create_table(
        "analysis_job_queue",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lock_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_letter_reason", sa.String(length=128), nullable=True),
        sa.Column("dead_letter_description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_job_queue_ready",
        "analysis_job_queue",
        ["available_at", "id"],
        postgresql_where=sa.text("dead_lettered_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the queue; queued and dead-lettered messages are lost."""
    op.drop_index("ix_analysis_job_queue_ready", table_name="analysis_job_queue")
    op.drop_table("analysis_job_queue")
