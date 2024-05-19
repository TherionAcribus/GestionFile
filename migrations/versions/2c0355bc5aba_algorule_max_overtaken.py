"""Algorule > Max_overtaken

Revision ID: 2c0355bc5aba
Revises: 69e45a6cf854
Create Date: 2024-05-19 13:32:49.167903

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2c0355bc5aba'
down_revision = '69e45a6cf854'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('algo_rule', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_overtaken', sa.Integer(), nullable=False, server_default="999"))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('algo_rule', schema=None) as batch_op:
        batch_op.drop_column('max_overtaken')

    # ### end Alembic commands ###
