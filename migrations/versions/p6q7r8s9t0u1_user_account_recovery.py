"""Récupération de compte : changement de mot de passe obligatoire

Deux colonnes sur app_users :
- must_change_password : le mot de passe est temporaire (réinitialisé par un
  admin, la commande de secours ou ADMIN_RESET_PASSWORD) ;
- env_reset_marker : empreinte de la dernière valeur d'ADMIN_RESET_PASSWORD
  appliquée, pour ne pas la réappliquer à chaque redémarrage.

Strictement additive.

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-10-19 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'p6q7r8s9t0u1'
down_revision = 'o5p6q7r8s9t0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('app_users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('env_reset_marker', sa.String(length=64),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('app_users', schema=None) as batch_op:
        batch_op.drop_column('env_reset_marker')
        batch_op.drop_column('must_change_password')
