"""Activity > changement nom specific message

Revision ID: c94c714a070a
Revises: bd32931a1806
Create Date: 2024-08-24 07:14:56.658739

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c94c714a070a'
down_revision = 'bd32931a1806'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.add_column(sa.Column('specific_message', sa.String(length=255), nullable=True))
        batch_op.drop_column('activity_specific_message')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.add_column(sa.Column('activity_specific_message', mysql.VARCHAR(length=255), nullable=True))
        batch_op.drop_column('specific_message')

    # ### end Alembic commands ###
