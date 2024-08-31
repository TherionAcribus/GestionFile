"""Language > traduction -> translation

Revision ID: 529f2e885fb5
Revises: c94c714a070a
Create Date: 2024-08-31 15:40:39.257870

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '529f2e885fb5'
down_revision = 'c94c714a070a'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('language', schema=None) as batch_op:
        batch_op.add_column(sa.Column('translation', sa.String(length=50), nullable=False))
        batch_op.drop_column('traduction')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('language', schema=None) as batch_op:
        batch_op.add_column(sa.Column('traduction', mysql.VARCHAR(length=50), nullable=False))
        batch_op.drop_column('translation')

    # ### end Alembic commands ###
