"""Messagerie : colonne delivered_at sur les destinataires.

« Remis » — l'app du destinataire est connectée et a appris que le message
existe — est désormais distingué de « Lu » (consultation réelle). La
colonne horodate cette réception ; elle est posée côté serveur dès que
l'app destinataire interroge l'état ou le fil.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-10-05 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op


revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_message_recipient",
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("app_message_recipient", "delivered_at")
