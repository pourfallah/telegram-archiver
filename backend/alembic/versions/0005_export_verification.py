"""add export verification columns

Revision ID: 0005_export_verification
Revises: 0004_change_import_id_to_bigint
Create Date: 2026-08-26 10:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '0005_export_verification'
down_revision = '0004_change_import_id_to_bigint'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat_exports', sa.Column('verified', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('chat_exports', sa.Column('verification', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_exports', 'verification')
    op.drop_column('chat_exports', 'verified')
