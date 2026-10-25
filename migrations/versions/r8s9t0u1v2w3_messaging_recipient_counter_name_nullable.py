"""Messagerie : recipient_counter_name devient nullable.

Un destinataire hors ligne n'a pas de comptoir courant : l'envoi direct et
le broadcast ``scope="all"`` créent désormais des destinataires sans
comptoir.

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-10-04 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "app_message_recipient",
        "recipient_counter_name",
        existing_type=sa.String(length=20),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        "app_message_recipient",
        "recipient_counter_name",
        existing_type=sa.String(length=20),
        nullable=False,
    )
