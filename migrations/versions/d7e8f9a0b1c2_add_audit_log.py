"""Table audit_log pour le journal des actions sensibles (point 7 — Phase 8)

Revision ID: d7e8f9a0b1c2
Revises: e5f6a7b8c9d0
Create Date: 2026-07-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7e8f9a0b1c2'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('resource', sa.String(length=40), nullable=False),
        sa.Column('target', sa.String(length=64), nullable=True),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('details', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_timestamp', 'audit_log', ['timestamp'])
    op.create_index('ix_audit_log_resource', 'audit_log', ['resource'])
    op.create_index('ix_audit_log_resource_timestamp', 'audit_log', ['resource', 'timestamp'])


def downgrade():
    op.drop_index('ix_audit_log_resource_timestamp', table_name='audit_log')
    op.drop_index('ix_audit_log_resource', table_name='audit_log')
    op.drop_index('ix_audit_log_timestamp', table_name='audit_log')
    op.drop_table('audit_log')
