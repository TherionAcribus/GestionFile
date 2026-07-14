"""Ajout de la table spotify_token (jetons OAuth Spotify côté serveur)

Déplace le stockage du jeton OAuth Spotify de la session Flask (cookie signé
côté client) vers la base de données. L'officine ne connecte qu'un seul compte
Spotify : une unique ligne (id == 1) porte le jeton sérialisé en JSON. Aucune
donnée n'est migrée : les anciens jetons vivaient dans les cookies de session et
seront simplement re-générés à la prochaine connexion Spotify.

Revision ID: e5f6a7b8c9d0
Revises: c1d2e3f4a5b6
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'spotify_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_info', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('spotify_token')
