"""Fusion des têtes : contraintes algo_rule + messagerie App Comptoir.

``a9b8c7d6e5f4`` (CHECK sur algo_rule) et ``f0a1b2c3d4e5`` (tables de
messagerie) descendent toutes les deux de ``e7f8a9b0c1d2`` : Alembic avait
deux têtes et ``upgrade head`` — utilisé au déploiement (manage.py,
docker-compose) — échouait avec « Multiple head revisions ».

Revision ID: b2c3d4e5f6a7
Revises: a9b8c7d6e5f4, f0a1b2c3d4e5
Create Date: 2026-09-25 00:00:00.000000

"""


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = ('a9b8c7d6e5f4', 'f0a1b2c3d4e5')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
