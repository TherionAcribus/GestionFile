"""Ajout de dashboard_user_layout (disposition personnelle du tableau de bord)

Une ligne par administrateur : ordre des cartes et cartes masquées propres à
l'utilisateur, qui priment sur la disposition commune
(dashboard_cards.visible/position). Les réglages par carte
(dashboard_cards.settings) restent communs. Table strictement additive.

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-10-21 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'dashboard_user_layout',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('layout', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'], ['app_users.id'], name='fk_dashboard_user_layout_user_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_dashboard_user_layout_user_id',
        'dashboard_user_layout', ['user_id'], unique=True)


def downgrade():
    op.drop_index('ix_dashboard_user_layout_user_id',
                  table_name='dashboard_user_layout')
    op.drop_table('dashboard_user_layout')
