"""Table queue_revision pour la convergence de l'état de la file

Revision ID: a7e4d9f21c3b
Revises: f3a9c1e2b7d4
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7e4d9f21c3b'
down_revision = 'f3a9c1e2b7d4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'queue_revision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Amorçage de l'unique ligne (id=1) pour que bump_queue_revision/
    # get_queue_revision trouvent toujours la ligne à incrémenter/lire.
    queue_revision = sa.table(
        'queue_revision',
        sa.column('id', sa.Integer),
        sa.column('revision', sa.BigInteger),
    )
    op.bulk_insert(queue_revision, [{'id': 1, 'revision': 0}])


def downgrade():
    op.drop_table('queue_revision')
