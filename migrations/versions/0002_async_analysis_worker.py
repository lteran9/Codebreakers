"""Add worker leases, transient job inputs, and the transactional outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_async_analysis_worker"
down_revision: str | None = "0001_analysis_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add claim lease columns, the inputs table, and the outbox table."""
    op.add_column(
        "analysis_jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "analysis_job_inputs",
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["analysis_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "analysis_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["analysis_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_outbox_job_id", "analysis_outbox", ["job_id"])


def downgrade() -> None:
    """Remove worker support; pending inputs and unpublished messages are lost."""
    op.drop_index("ix_analysis_outbox_job_id", table_name="analysis_outbox")
    op.drop_table("analysis_outbox")
    op.drop_table("analysis_job_inputs")
    op.drop_column("analysis_jobs", "lease_expires_at")
    op.drop_column("analysis_jobs", "attempts")
