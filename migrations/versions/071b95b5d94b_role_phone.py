"""Role > phone

Revision ID: 071b95b5d94b
Revises: 77bb4e949839
Create Date: 2024-12-25 17:18:08.172958

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '071b95b5d94b'
down_revision = '77bb4e949839'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('role', schema=None) as batch_op:
        batch_op.add_column(sa.Column('admin_phone', sa.String(length=10), nullable=False))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('role', schema=None) as batch_op:
        batch_op.drop_column('admin_phone')

    # ### end Alembic commands ###
