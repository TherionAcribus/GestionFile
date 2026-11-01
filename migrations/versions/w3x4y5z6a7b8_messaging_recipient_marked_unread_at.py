"""Messagerie : colonne marked_unread_at sur les destinataires.

« Marquer comme non lu » : le destinataire remet une conversation en non lu
pour y revenir plus tard. Colonne séparée de read_at : l'expéditeur garde
son accusé « Lu » (la lecture a bien eu lieu), seul le compteur de non-lus
du destinataire change.

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-11-01 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_message_recipient",
        sa.Column("marked_unread_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("app_message_recipient", "marked_unread_at")
