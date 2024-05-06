"""ManyToMany

Revision ID: 340b612d335e
Revises: 
Create Date: 2024-05-06 13:06:26.677479

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '340b612d335e'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.drop_column('counter_number')

    with op.batch_alter_table('pharmacist', schema=None) as batch_op:
        batch_op.drop_column('activity')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('pharmacist', schema=None) as batch_op:
        batch_op.add_column(sa.Column('activity', sa.VARCHAR(length=100), nullable=True))

    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('counter_number', sa.INTEGER(), nullable=True))

    # ### end Alembic commands ###