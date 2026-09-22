"""Cle d'idempotence du transfert Patient -> PatientHistory

La copie vers l'historique et la purge de la file etaient validees en deux
transactions distinctes : si la suppression echouait apres une copie reussie,
une relance recopiait les memes patients (doublons d'historique). Le transfert
est desormais atomique (une seule transaction cote service), et cette colonne
verrouille le resultat : un patient ne peut produire qu'une seule ligne
d'historique — une relance apres echec partiel leve IntegrityError au lieu de
dupliquer.

NULL tolere : les lignes historiques anterieures a la migration et celles qui
ne proviennent pas du transfert restent valides (MySQL traite les NULL comme
distincts dans un index unique).

Revision ID: a1b2c3d4e5f7
Revises: f7a8b9c0d1e2
Create Date: 2026-09-22 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f7'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('patient_history') as batch_op:
        batch_op.add_column(sa.Column('patient_source_id', sa.Integer(), nullable=True))

    op.create_unique_constraint(
        'uq_patient_history_source', 'patient_history', ['patient_source_id']
    )


def downgrade():
    op.drop_constraint(
        'uq_patient_history_source', 'patient_history', type_='unique'
    )
    with op.batch_alter_table('patient_history') as batch_op:
        batch_op.drop_column('patient_source_id')
