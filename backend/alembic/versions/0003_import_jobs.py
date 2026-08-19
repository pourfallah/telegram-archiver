"""import jobs table

Revision ID: 0003_import_jobs
Revises: 0002_grouped_id
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_import_jobs"
down_revision = "0002_grouped_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_export_id", sa.BigInteger(), sa.ForeignKey("chat_exports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_account_id", sa.BigInteger(), sa.ForeignKey("telegram_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_peer_id", sa.BigInteger(), nullable=True),
        sa.Column("message_limit", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(24), default="queued"),
        sa.Column("options", sa.JSON(), default={}),
        sa.Column("progress", sa.JSON(), nullable=True),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_import_jobs_source_export", "import_jobs", ["source_export_id"])
    op.create_index("ix_import_jobs_target_account", "import_jobs", ["target_account_id"])


def downgrade() -> None:
    op.drop_index("ix_import_jobs_target_account", table_name="import_jobs")
    op.drop_index("ix_import_jobs_source_export", table_name="import_jobs")
    op.drop_table("import_jobs")