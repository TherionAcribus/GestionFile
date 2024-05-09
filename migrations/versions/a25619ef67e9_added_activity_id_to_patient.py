"""Added activity_id to Patient

Revision ID: a25619ef67e9
Revises: 1a63c5961ec5
Create Date: 2024-05-09 17:44:39.441221

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a25619ef67e9'
down_revision = '1a63c5961ec5'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('activity_id', sa.Integer(), nullable=False))
        batch_op.create_foreign_key('fk_patient_activity_id', 'activity', ['activity_id'], ['id'])
        batch_op.drop_column('visit_reason')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('patient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('visit_reason', sa.VARCHAR(length=120), nullable=False))
        batch_op.drop_constraint('fk_patient_activity_id', type_='foreignkey')
        batch_op.drop_column('activity_id')

    # ### end Alembic commands ###