"""Ajout de counter_realtime_health (sante du temps reel des comptoirs)

Compteurs envoyes par les App comptoir (evenements recus, trous de revision,
coupures, temps connecte), agreges par comptoir et par heure. Table
strictement additive ; purge au-dela de 30 jours par l'API de reception.

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-10-18 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'o5p6q7r8s9t0'
down_revision = 'n4o5p6q7r8s9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'counter_realtime_health',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('counter_id', sa.Integer(), nullable=False),
        sa.Column('hour', sa.DateTime(), nullable=False),
        sa.Column('events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gaps', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('missed_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reconnect_gaps', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stale_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('disconnects', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('connected_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('reports', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('app_version', sa.String(length=40), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['counter_id'], ['counter.id'],
                                name='fk_counter_rt_health_counter',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('counter_id', 'hour', name='uq_counter_rt_health_hour'),
    )
    op.create_index('ix_counter_realtime_health_hour',
                    'counter_realtime_health', ['hour'], unique=False)


def downgrade():
    op.drop_index('ix_counter_realtime_health_hour',
                  table_name='counter_realtime_health')
    op.drop_table('counter_realtime_health')
