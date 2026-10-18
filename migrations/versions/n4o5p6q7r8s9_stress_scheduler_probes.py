"""Ajout de stress_test_run.scheduler_probes_sent (sondes temps reel)

Compteur des sondes Socket.IO emises par le processus planificateur pendant
un test de charge : permet de distinguer un planificateur muet d'evenements
emis mais jamais arrives aux clients (absence de relais RabbitMQ). Colonne
strictement additive, 0 par defaut.

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-10-18 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'n4o5p6q7r8s9'
down_revision = 'm3n4o5p6q7r8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('stress_test_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'scheduler_probes_sent', sa.Integer(), nullable=False,
            server_default='0'))


def downgrade():
    with op.batch_alter_table('stress_test_run', schema=None) as batch_op:
        batch_op.drop_column('scheduler_probes_sent')
