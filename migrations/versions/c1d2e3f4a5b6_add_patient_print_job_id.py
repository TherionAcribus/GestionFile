"""Ajout de print_job_id sur patient (corrélation inscription <-> impression)

Permet le flux pending -> impression locale -> confirmation serveur :
l'inscription est d'abord créée en status='pending' avec un print_job_id, la
Borne imprime, puis confirme le résultat via /patient/confirm_print qui retrouve
le patient par ce print_job_id pour l'activer (ou l'annuler). Unique : sert
aussi de garde-fou d'idempotence.

Revision ID: c1d2e3f4a5b6
Revises: b8f2c3d4e5a6
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'b8f2c3d4e5a6'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table : requis pour ALTER sous SQLite (recréation de table).
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('print_job_id', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_patient_print_job_id', ['print_job_id'], unique=True)


def downgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_index('ix_patient_print_job_id')
        batch_op.drop_column('print_job_id')
