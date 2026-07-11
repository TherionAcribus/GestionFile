"""Index de performance sur la table patient

Deux index composites pour les requêtes chaudes de la file (rejouées à chaque
mutation) :
  - (status, timestamp) : filtre status='standing' + ORDER BY timestamp, et
    plus généralement tout filtre status=? / status IN (...).
  - (counter_id, status) : requêtes propres à un comptoir
    (WHERE counter_id=? AND status ...).

Revision ID: b8f2c3d4e5a6
Revises: a7e4d9f21c3b
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b8f2c3d4e5a6'
down_revision = 'a7e4d9f21c3b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_patient_status_timestamp', 'patient', ['status', 'timestamp'], unique=False
    )
    op.create_index(
        'ix_patient_counter_status', 'patient', ['counter_id', 'status'], unique=False
    )


def downgrade():
    op.drop_index('ix_patient_counter_status', table_name='patient')
    op.drop_index('ix_patient_status_timestamp', table_name='patient')
