"""Ajout de admin_onboarding_state (parcours de configuration guidée)

Suivi par compte administrateur de la progression pédagogique dans les pages
de configuration existantes : statut du parcours, étape courante, états des
étapes (JSON) et version de concurrence optimiste. Une ligne par utilisateur
(unique sur user_id). Table strictement additive : aucune donnée de pharmacie
n'est modifiée.

Revision ID: e9f0a1b2c3d4
Revises: d1e2f3a4b5c6
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e9f0a1b2c3d4'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admin_onboarding_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('current_step', sa.String(length=32), nullable=True),
        sa.Column('steps_state', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'], ['app_users.id'], name='fk_admin_onboarding_user_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_admin_onboarding_state_user_id',
        'admin_onboarding_state', ['user_id'], unique=True)


def downgrade():
    op.drop_index('ix_admin_onboarding_state_user_id',
                  table_name='admin_onboarding_state')
    op.drop_table('admin_onboarding_state')
