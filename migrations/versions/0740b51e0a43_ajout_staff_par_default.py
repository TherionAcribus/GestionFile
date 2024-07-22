"""ajout staff par default

Revision ID: 0740b51e0a43
Revises: 2b7255f3a5d3
Create Date: 2024-07-22 10:39:57.460840

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0740b51e0a43'
down_revision = '2b7255f3a5d3'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('role')
    op.drop_table('roles_users')
    op.drop_table('user')
    with op.batch_alter_table('pharmacist', schema=None) as batch_op:
        batch_op.add_column(sa.Column('default', sa.Boolean(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('pharmacist', schema=None) as batch_op:
        batch_op.drop_column('default')

    op.create_table('user',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('email', sa.VARCHAR(length=255), nullable=True),
    sa.Column('username', sa.VARCHAR(length=255), nullable=True),
    sa.Column('password', sa.VARCHAR(length=255), nullable=False),
    sa.Column('last_login_at', sa.DATETIME(), nullable=True),
    sa.Column('current_login_at', sa.DATETIME(), nullable=True),
    sa.Column('last_login_ip', sa.VARCHAR(length=100), nullable=True),
    sa.Column('current_login_ip', sa.VARCHAR(length=100), nullable=True),
    sa.Column('login_count', sa.INTEGER(), nullable=True),
    sa.Column('active', sa.BOOLEAN(), nullable=True),
    sa.Column('fs_uniquifier', sa.VARCHAR(length=255), nullable=False),
    sa.Column('confirmed_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('fs_uniquifier'),
    sa.UniqueConstraint('username')
    )
    op.create_table('roles_users',
    sa.Column('user_id', sa.INTEGER(), nullable=True),
    sa.Column('role_id', sa.INTEGER(), nullable=True),
    sa.ForeignKeyConstraint(['role_id'], ['role.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], )
    )
    op.create_table('role',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('name', sa.VARCHAR(length=80), nullable=True),
    sa.Column('description', sa.VARCHAR(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    # ### end Alembic commands ###
