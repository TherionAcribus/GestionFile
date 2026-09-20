"""Table printer_status : historique persistant des statuts imprimante

Remplace l'état runtime app.config["PRINTER_INFOS"] / ["PRINTER_ERROR"],
qui était propre à chaque process (invisible partiellement en multi-worker
et perdu au redémarrage).

Revision ID: f7a8b9c0d1e2
Revises: e9a1b2c3d4f5
Create Date: 2026-09-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a8b9c0d1e2'
down_revision = 'e9a1b2c3d4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'printer_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('borne_id', sa.String(length=80), nullable=True),
        sa.Column('error_code', sa.String(length=40), nullable=False),
        sa.Column('is_error', sa.Boolean(), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('printer_status')
