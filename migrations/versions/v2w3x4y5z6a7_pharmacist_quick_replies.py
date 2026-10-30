"""Équipe : phrases rapides personnelles de la messagerie.

Liste de textes propre à chaque équipier, proposée dans le menu « ⚡ » de la
messagerie de l'App Comptoir (cf. quick_replies.py). NULL = aucune phrase.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-10-30 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pharmacist")}
    if "quick_replies" in columns:
        return
    op.add_column(
        "pharmacist",
        sa.Column("quick_replies", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("pharmacist", "quick_replies")
