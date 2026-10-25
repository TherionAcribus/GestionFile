"""Messagerie : colonne deleted_at sur les messages.

Retrait logique d'un message envoyé par erreur par son expéditeur :
l'enregistrement (et sa date de retrait) est conservé pour l'audit, le
contenu disparaît seulement des fils, états et compteurs de non-lus —
jamais de suppression physique.

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-10-07 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_message",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("app_message", "deleted_at")
