"""Editeur visuel des pages publiques

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-24 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'page_editor_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_key', sa.String(length=20), nullable=False),
        sa.Column('draft_json', sa.JSON(), nullable=True),
        sa.Column('draft_base_hash', sa.String(length=64), nullable=True),
        sa.Column('draft_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('draft_updated_by_id', sa.Integer(), nullable=True),
        sa.Column('draft_updated_at', sa.DateTime(), nullable=True),
        sa.Column('published_revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('published_by_id', sa.Integer(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['draft_updated_by_id'], ['app_users.id']),
        sa.ForeignKeyConstraint(['published_by_id'], ['app_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_key'),
    )
    op.create_table(
        'page_editor_revision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_key', sa.String(length=20), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('published_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['published_by_id'], ['app_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_key', 'revision', name='uq_page_editor_revision'),
    )
    op.create_index(
        'ix_page_editor_revision_page_key',
        'page_editor_revision',
        ['page_key'],
    )
    op.create_index(
        'ix_page_editor_page_revision',
        'page_editor_revision',
        ['page_key', 'revision'],
    )


def downgrade():
    op.drop_index('ix_page_editor_page_revision', table_name='page_editor_revision')
    op.drop_index('ix_page_editor_revision_page_key', table_name='page_editor_revision')
    op.drop_table('page_editor_revision')
    op.drop_table('page_editor_state')
