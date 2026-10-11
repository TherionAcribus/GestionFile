"""Unicité du nom des cartes du tableau de bord (dédoublonnage préalable : on garde la carte au plus petit id)

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-10-20 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'k1l2m3n4o5p6'
down_revision = 'j0k1l2m3n4o5'
branch_labels = None
depends_on = None


def upgrade():
    # MySQL refuse une sous-requête sur la table cible d'un DELETE : la table
    # dérivée « keep » contourne la restriction (valide aussi sous SQLite).
    op.execute(
        "DELETE FROM dashboard_cards WHERE id NOT IN "
        "(SELECT id FROM (SELECT MIN(id) AS id FROM dashboard_cards GROUP BY name) AS keep)"
    )
    with op.batch_alter_table('dashboard_cards', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_dashboard_cards_name', ['name'])


def downgrade():
    with op.batch_alter_table('dashboard_cards', schema=None) as batch_op:
        batch_op.drop_constraint('uq_dashboard_cards_name', type_='unique')
