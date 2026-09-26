"""Ajout de patient_step (journal des étapes du parcours)

La ligne patient est réécrite à chaque étape : une redirection (renvoi en
file, transfert vers une autre activité) ou un retrait par le personnel
effaçait le passage par l'activité et le comptoir précédents. patient_step
consigne chaque clôture intermédiaire (issue, activité servie, comptoir,
horodatages). patient_id n'est pas une clé étrangère : la ligne patient est
purgée en fin de journée alors que les étapes doivent survivre.

Revision ID: d1e2f3a4b5c6
Revises: b7c8d9e0f1a2
Create Date: 2026-09-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'patient_step',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('activity_id', sa.Integer(), nullable=True),
        sa.Column('new_activity_id', sa.Integer(), nullable=True),
        sa.Column('counter_id', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('timestamp_counter', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_patient_step_patient_id', 'patient_step', ['patient_id'])


def downgrade():
    op.drop_index('ix_patient_step_patient_id', table_name='patient_step')
    op.drop_table('patient_step')
