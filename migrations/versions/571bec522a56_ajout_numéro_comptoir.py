"""Ajout numéro comptoir

Revision ID: 571bec522a56
Revises: 
Create Date: 2024-04-25 07:32:03.116875

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '571bec522a56'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('counter_number', sa.Integer(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_column('counter_number')

    # ### end Alembic commands ###
