"""Counter > ajouter d'un ordre2

Revision ID: 88806b5b7f60
Revises: 7067c7547c07
Create Date: 2024-08-11 10:25:18.860135

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '88806b5b7f60'
down_revision = '7067c7547c07'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('counter', schema=None) as batch_op:
        batch_op.add_column(sa.Column('order', sa.Integer(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('counter', schema=None) as batch_op:
        batch_op.drop_column('order')

    # ### end Alembic commands ###