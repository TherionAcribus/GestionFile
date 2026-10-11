"""Add page editor draft base snapshot

Revision ID: j0k1l2m3n4o5
Revises: i6j7k8l9m0n1
Create Date: 2026-10-15 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'j0k1l2m3n4o5'
down_revision = 'i6j7k8l9m0n1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('page_editor_state', schema=None) as batch_op:
        batch_op.add_column(sa.Column('draft_base_json', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('page_editor_state', schema=None) as batch_op:
        batch_op.drop_column('draft_base_json')
