"""Thèmes nommés de l'éditeur visuel

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-09-24 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = 'e7f8a9b0c1d2'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'page_editor_theme',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_key', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=True),
        sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['app_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_key', 'name', name='uq_page_editor_theme'),
    )
    op.create_index(
        'ix_page_editor_theme_page_key',
        'page_editor_theme',
        ['page_key'],
    )


def downgrade():
    op.drop_index('ix_page_editor_theme_page_key', table_name='page_editor_theme')
    op.drop_table('page_editor_theme')
