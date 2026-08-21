"""change import_id to BigInteger

Revision ID: 0004_change_import_id_to_bigint
Revises: 0003_import_jobs
Create Date: 2026-08-21 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004_change_import_id_to_bigint'
down_revision = '0003_import_jobs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change the import_id column to BigInteger
    op.alter_column('import_jobs', 'import_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=True)


def downgrade() -> None:
    # Revert back to Integer
    op.alter_column('import_jobs', 'import_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=True)