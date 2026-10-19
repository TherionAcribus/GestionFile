"""Ajout table staff_preference (profil de préférences par équipier)

L'App Comptoir y dépose les réglages personnels d'un équipier (raccourcis,
notifications, disposition…) pour les redescendre quand il se connecte sur
un autre poste. Une ligne par équipier ; dernière écriture gagnante.
Identité du poste volontairement absente (ni URL, ni comptoir, ni secret).

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-10-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q7r8s9t0u1v2'
down_revision = 'p6q7r8s9t0u1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'staff_preference' in inspector.get_table_names():
        return

    op.create_table(
        'staff_preference',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('staff_id', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['staff_id'], ['pharmacist.id'],
            name='fk_staff_preference_staff'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('staff_id', name='uq_staff_preference_staff')
    )
    op.create_index(
        'ix_staff_preference_staff_id',
        'staff_preference', ['staff_id'])


def downgrade():
    op.drop_index(
        'ix_staff_preference_staff_id', table_name='staff_preference')
    op.drop_table('staff_preference')
