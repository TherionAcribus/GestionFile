"""Messagerie : colonne urgent sur les messages.

Un message « urgent » reste un message ordinaire côté stockage : le
drapeau sert à l'App destinataire pour la notification persistante et le
rendu distinct du fil.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-10-06 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_message",
        sa.Column("urgent", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade():
    op.drop_column("app_message", "urgent")
