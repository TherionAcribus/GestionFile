"""Contraintes CHECK réelles sur algo_rule

Les CheckConstraint étaient déclarées en attributs de classe du modèle — hors
``__table_args__``, SQLAlchemy les ignorait : aucun CHECK n'existait en base
(valeurs négatives, min > max, priorité hors 1-5, créneau inversé acceptés).

``batch_alter_table`` fonctionne sur SQLite (recréation de table) comme sur
MySQL (ALTER TABLE ADD CONSTRAINT, CHECK réellement vérifié depuis 8.0.16).

Revision ID: a9b8c7d6e5f4
Revises: e7f8a9b0c1d2
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


_CONSTRAINTS = (
    ('ck_priority_rules_min_patients_nonneg', 'min_patients >= 0'),
    ('ck_priority_rules_min_max_patients', 'min_patients <= max_patients'),
    ('ck_priority_rules_max_overtaken_nonneg', 'max_overtaken >= 0'),
    ('ck_priority_rules_start_end_time', 'start_time < end_time'),
    ('ck_priority_rules_priority_level', 'priority_level BETWEEN 1 AND 5'),
)


def upgrade():
    with op.batch_alter_table('algo_rule') as batch_op:
        for name, condition in _CONSTRAINTS:
            batch_op.create_check_constraint(name, condition)


def downgrade():
    with op.batch_alter_table('algo_rule') as batch_op:
        for name, _ in _CONSTRAINTS:
            batch_op.drop_constraint(name, type_='check')
