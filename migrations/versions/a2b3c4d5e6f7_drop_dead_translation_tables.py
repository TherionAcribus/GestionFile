"""Suppression des tables de traduction historiques write-only

``text``, ``text_translation`` et ``text_interface`` étaient alimentées au
démarrage (``default_texts.json`` / ``default_translations.json``) et
embarquées dans les sauvegardes, mais aucun chemin de rendu ne les lisait :
les traductions affichées viennent toutes de ``translation`` (catalogue
collecté depuis les sources réelles). Leur suppression supprime un faux
système de traduction parallèle.

Revision ID: a2b3c4d5e6f7
Revises: f1e2d3c4b5a6
Create Date: 2026-09-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'f1e2d3c4b5a6'
branch_labels = None
depends_on = None

# Ordre : d'abord la table porteuse des clés étrangères vers text/language.
_TABLES = ("text_translation", "text_interface", "text")


def upgrade():
    # has_table : les tables ont historiquement été créées par create_all
    # plus que par migrations — la base d'une install peut les avoir sous
    # un nom ou un état légèrement différent.
    inspector = sa.inspect(op.get_bind())
    for table in _TABLES:
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("text"):
        op.create_table(
            "text",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("text_key", sa.String(100), nullable=False),
            sa.Column("text_value", sa.Text(), nullable=False),
            sa.UniqueConstraint("text_key", name="uq_text_key"),
        )
    if not inspector.has_table("text_interface"):
        op.create_table(
            "text_interface",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("text_id", sa.String(50), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
        )
    if not inspector.has_table("text_translation"):
        op.create_table(
            "text_translation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("text_id", sa.Integer(), nullable=False),
            sa.Column("language_id", sa.Integer(), nullable=False),
            sa.Column("translation", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["text_id"], ["text.id"],
                name="fk_text_translation_text_id", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["language_id"], ["language.id"],
                name="fk_text_translation_language_id", ondelete="CASCADE"),
        )
