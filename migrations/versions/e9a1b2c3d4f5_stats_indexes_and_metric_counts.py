"""Index d'archive et effectifs par metrique pour les statistiques

Trois changements, tous au service de la page /admin/stats :

  1. Index sur `patient_history`. La table d'archive n'en avait aucun hors PK,
     alors que toutes les lectures la filtrent sur une plage de `timestamp`
     (graphiques, job d'archivage) ou la trient dessus (historique detaille
     pagine) : chaque requete etait un balayage complet d'une table qui croit
     indefiniment.

  2. Effectifs par metrique sur `aggregated_stats`. La moyenne ponderee des
     durees utilisait `count` (tous les patients du jour) comme poids, alors
     que seuls les patients ayant les deux timestamps renseignes participent a
     la moyenne. Les nouvelles colonnes sont NULL pour les lignes existantes :
     le code retombe sur `count` dans ce cas.

  3. Unicite (date, category_type, category_id) sur `aggregated_stats`. Le job
     d'archivage rejoue sur une date deja traitee inserait des doublons, que la
     fusion detaille/compresse additionne silencieusement.

Revision ID: e9a1b2c3d4f5
Revises: d7e8f9a0b1c2
Create Date: 2026-09-05 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e9a1b2c3d4f5'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_patient_history_timestamp', 'patient_history', ['timestamp'], unique=False
    )
    op.create_index(
        'ix_patient_history_activity_timestamp', 'patient_history',
        ['activity_id', 'timestamp'], unique=False
    )
    op.create_index(
        'ix_patient_history_counter_timestamp', 'patient_history',
        ['counter_id', 'timestamp'], unique=False
    )

    with op.batch_alter_table('aggregated_stats') as batch_op:
        batch_op.add_column(sa.Column('count_waiting_time', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('count_counter_time', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('count_total_time', sa.Integer(), nullable=True))

    # Les doublons deja presents empecheraient la creation de la contrainte :
    # on ne garde que la ligne de plus petit id pour chaque cle.
    op.execute(
        """
        DELETE a FROM aggregated_stats a
        JOIN aggregated_stats b
          ON a.date = b.date
         AND a.category_type = b.category_type
         AND a.category_id = b.category_id
         AND a.id > b.id
        """
    )

    op.create_unique_constraint(
        'uq_aggregated_stats_date_type_category', 'aggregated_stats',
        ['date', 'category_type', 'category_id']
    )


def downgrade():
    op.drop_constraint(
        'uq_aggregated_stats_date_type_category', 'aggregated_stats', type_='unique'
    )

    with op.batch_alter_table('aggregated_stats') as batch_op:
        batch_op.drop_column('count_total_time')
        batch_op.drop_column('count_counter_time')
        batch_op.drop_column('count_waiting_time')

    op.drop_index('ix_patient_history_counter_timestamp', table_name='patient_history')
    op.drop_index('ix_patient_history_activity_timestamp', table_name='patient_history')
    op.drop_index('ix_patient_history_timestamp', table_name='patient_history')
