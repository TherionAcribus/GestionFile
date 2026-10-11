"""Suppression des colonnes mortes size/color des cartes du tableau de bord

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-10-20 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dashboard_cards', schema=None) as batch_op:
        batch_op.drop_column('size')
        batch_op.drop_column('color')


def downgrade():
    with op.batch_alter_table('dashboard_cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('size', sa.String(20), nullable=False, server_default='36'))
        batch_op.add_column(sa.Column('color', sa.String(20), nullable=True, server_default='bg-white'))
