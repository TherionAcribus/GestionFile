"""Élargit language.code de String(2) à String(5)

``Translation.language_code`` est déjà en String(5) : la limite à 2
caractères de ``language.code`` empêchait les codes régionalisés
(``pt-br``, ``zh-tw``) que la table de traduction accepte pourtant.
La colonne est élargie pour harmoniser les deux.

Revision ID: f1e2d3c4b5a6
Revises: b2c3d4e5f6a7
Create Date: 2026-09-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1e2d3c4b5a6'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table : ALTER TABLE natif sous MySQL, recopie de table
    # sous SQLite (qui ne supporte pas ALTER COLUMN).
    with op.batch_alter_table('language') as batch_op:
        batch_op.alter_column(
            'code',
            existing_type=sa.String(2),
            type_=sa.String(5),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('language') as batch_op:
        batch_op.alter_column(
            'code',
            existing_type=sa.String(5),
            type_=sa.String(2),
            existing_nullable=False,
        )
