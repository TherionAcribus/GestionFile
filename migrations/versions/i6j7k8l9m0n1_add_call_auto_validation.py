"""Add automatic call validation deadline

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-10-10 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'i6j7k8l9m0n1'
down_revision = 'h5i6j7k8l9m0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'call_auto_validate_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column(
            'call_extension_count', sa.Integer(), nullable=False,
            server_default='0'))
        batch_op.create_index(
            'ix_patient_status_auto_validate_at',
            ['status', 'call_auto_validate_at'], unique=False)


def downgrade():
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_index('ix_patient_status_auto_validate_at')
        batch_op.drop_column('call_extension_count')
        batch_op.drop_column('call_auto_validate_at')
