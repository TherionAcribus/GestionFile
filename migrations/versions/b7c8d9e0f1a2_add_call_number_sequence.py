"""Ajout de call_number_sequence (numérotation atomique)

Compteur persistant des numéros d'appel, par journée (day) et par série
(scope : 'simple' ou lettre d'activité). L'attribution passe par un verrou
de ligne + incrément au lieu de relire « le dernier patient + 1 » : fin des
doublons entre inscriptions concurrentes et des numéros réattribués après
suppression. L'unicité (day, scope) départage la création concurrente de la
ligne.

Revision ID: b7c8d9e0f1a2
Revises: b3c4d5e6f7a8
Create Date: 2026-09-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'call_number_sequence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('value', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'scope', name='uq_call_number_sequence_day_scope'),
    )


def downgrade():
    op.drop_table('call_number_sequence')
