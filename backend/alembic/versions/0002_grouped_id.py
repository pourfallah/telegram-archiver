"""canonical archive: add messages.grouped_id

Revision ID: 0002_grouped_id
Revises: 9c17f02da1a1
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_grouped_id"
down_revision = "9c17f02da1a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("grouped_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_messages_grouped_id", "messages", ["grouped_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_grouped_id", table_name="messages")
    op.drop_column("messages", "grouped_id")