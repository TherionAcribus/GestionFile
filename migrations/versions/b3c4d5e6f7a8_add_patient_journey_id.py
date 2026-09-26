"""Ajout de journey_id sur patient (unicité du parcours borne)

Le parcours borne porte un UUID (page des boutons -> validation -> QR ->
impression). Désormais il est stocké sur le patient créé : rejeu réseau,
second téléphone scannant le même QR, ou bascule impression <-> scan sur le
même parcours retrouvent l'inscription existante au lieu d'en créer une
seconde. Unique : sert aussi de garde-fou en cas de requêtes concurrentes.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table : requis pour ALTER sous SQLite (recréation de table).
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('journey_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_patient_journey_id', ['journey_id'], unique=True)


def downgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_index('ix_patient_journey_id')
        batch_op.drop_column('journey_id')
