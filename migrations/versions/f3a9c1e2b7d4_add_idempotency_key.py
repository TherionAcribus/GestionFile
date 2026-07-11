"""Table idempotency_key pour les commandes non rejouables

Revision ID: f3a9c1e2b7d4
Revises: abc04012148c
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f3a9c1e2b7d4'
down_revision = 'abc04012148c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'idempotency_key',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('counter_id', sa.Integer(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('idempotency_key')
