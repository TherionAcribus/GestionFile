"""Ajout Table Css Variable Patient

Revision ID: 75932693183c
Revises: d4c30184184c
Create Date: 2024-11-01 16:21:56.436963

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '75932693183c'
down_revision = 'd4c30184184c'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('patient_css_variable',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('variable', sa.String(length=50), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('patient_css_variable')
    # ### end Alembic commands ###
