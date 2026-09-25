"""Messagerie instantanée de l'App Comptoir.

Revision ID: f0a1b2c3d4e5
Revises: e7f8a9b0c1d2
Create Date: 2026-09-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = "f0a1b2c3d4e5"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_message_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sender_staff_id", sa.Integer(), nullable=True),
        sa.Column("sender_counter_id", sa.Integer(), nullable=True),
        sa.Column("sender_name", sa.String(length=50), nullable=False),
        sa.Column("sender_counter_name", sa.String(length=20), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('direct', 'broadcast')", name="ck_app_message_kind",
        ),
        sa.ForeignKeyConstraint(
            ["sender_counter_id"], ["counter.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sender_staff_id"], ["pharmacist.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_message_id"),
    )
    op.create_index("ix_app_message_created_at", "app_message", ["created_at"])

    op.create_table(
        "app_message_recipient",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("recipient_staff_id", sa.Integer(), nullable=True),
        sa.Column("recipient_counter_id", sa.Integer(), nullable=True),
        sa.Column("recipient_name", sa.String(length=50), nullable=False),
        sa.Column("recipient_counter_name", sa.String(length=20), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"], ["app_message.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_counter_id"], ["counter.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_staff_id"], ["pharmacist.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id", "recipient_staff_id",
            name="uq_app_message_recipient_staff",
        ),
    )
    op.create_index(
        "ix_app_message_recipient_message_id",
        "app_message_recipient", ["message_id"],
    )
    op.create_index(
        "ix_app_message_recipient_recipient_staff_id",
        "app_message_recipient", ["recipient_staff_id"],
    )

    op.create_table(
        "app_messaging_presence",
        sa.Column("client_instance_id", sa.String(length=36), nullable=False),
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("counter_id", sa.Integer(), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["counter_id"], ["counter.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"], ["pharmacist.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_instance_id"),
    )
    op.create_index(
        "ix_app_messaging_presence_staff_id",
        "app_messaging_presence", ["staff_id"],
    )
    op.create_index(
        "ix_app_messaging_presence_last_seen_at",
        "app_messaging_presence", ["last_seen_at"],
    )


def downgrade():
    op.drop_index(
        "ix_app_messaging_presence_last_seen_at",
        table_name="app_messaging_presence",
    )
    op.drop_index(
        "ix_app_messaging_presence_staff_id",
        table_name="app_messaging_presence",
    )
    op.drop_table("app_messaging_presence")
    op.drop_index(
        "ix_app_message_recipient_recipient_staff_id",
        table_name="app_message_recipient",
    )
    op.drop_index(
        "ix_app_message_recipient_message_id",
        table_name="app_message_recipient",
    )
    op.drop_table("app_message_recipient")
    op.drop_index("ix_app_message_created_at", table_name="app_message")
    op.drop_table("app_message")
