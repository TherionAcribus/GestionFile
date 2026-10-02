"""Add administrable stress tests

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-10-02 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'h5i6j7k8l9m0'
down_revision = 'g4h5i6j7k8l9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('role', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'admin_performance', sa.Boolean(), nullable=False,
            server_default=sa.false()))

    # Le droit a risque eleve est accorde uniquement au role admin existant.
    op.execute(sa.text(
        "UPDATE role SET admin_performance = true WHERE name = 'admin'"))

    op.create_table(
        'stress_test_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('scenario', sa.String(length=32), nullable=False),
        sa.Column('profile', sa.String(length=32), nullable=False),
        sa.Column('requested_parameters', sa.JSON(), nullable=False),
        sa.Column('applied_parameters', sa.JSON(), nullable=False),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column('target_url', sa.String(length=512), nullable=False),
        sa.Column('application_version', sa.String(length=80), nullable=True),
        sa.Column('config_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=True),
        sa.Column('stop_reason', sa.String(length=80), nullable=True),
        sa.Column('stop_requested', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('stopping_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('runner_heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('error_message', sa.String(length=500), nullable=True),
        sa.Column('fixture_ids', sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "state IN ('queued','preparing','running','stopping','completed',"
            "'failed','aborted','interrupted')", name='ck_stress_run_state'),
        sa.ForeignKeyConstraint(['requested_by_id'], ['app_users.id'],
                                name='fk_stress_run_requested_by'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index('ix_stress_test_run_uuid', 'stress_test_run', ['uuid'], unique=True)
    op.create_index('ix_stress_test_run_requested_by_id', 'stress_test_run', ['requested_by_id'])
    op.create_index('ix_stress_test_run_state', 'stress_test_run', ['state'])
    op.create_index('ix_stress_run_state_created', 'stress_test_run', ['state', 'created_at'])
    op.create_index('ix_stress_test_run_created_at', 'stress_test_run', ['created_at'])

    op.create_table(
        'stress_test_sample',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('elapsed_seconds', sa.Integer(), nullable=False),
        sa.Column('active_users', sa.Integer(), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('rps', sa.Float(), nullable=False),
        sa.Column('errors', sa.Integer(), nullable=False),
        sa.Column('error_rate', sa.Float(), nullable=False),
        sa.Column('latency_p50_ms', sa.Float(), nullable=True),
        sa.Column('latency_p95_ms', sa.Float(), nullable=True),
        sa.Column('latency_p99_ms', sa.Float(), nullable=True),
        sa.Column('latency_max_ms', sa.Float(), nullable=True),
        sa.Column('web_cpu_percent', sa.Float(), nullable=True),
        sa.Column('web_memory_bytes', sa.BigInteger(), nullable=True),
        sa.Column('web_memory_limit_bytes', sa.BigInteger(), nullable=True),
        sa.Column('web_threads', sa.Integer(), nullable=True),
        sa.Column('ready', sa.Boolean(), nullable=False),
        sa.Column('db_connections', sa.Integer(), nullable=True),
        sa.Column('db_activity', sa.Integer(), nullable=True),
        sa.Column('pool_checked_out', sa.Integer(), nullable=True),
        sa.Column('socket_connections', sa.Integer(), nullable=True),
        sa.Column('socket_reconnections', sa.Integer(), nullable=True),
        sa.Column('endpoint_summary', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['stress_test_run.id'],
                                name='fk_stress_sample_run', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'elapsed_seconds',
                            name='uq_stress_sample_run_second'),
    )
    op.create_index('ix_stress_test_sample_run_id', 'stress_test_sample', ['run_id'])

    op.create_table(
        'stress_test_lease',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('runner_id', sa.String(length=80), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['stress_test_run.id'],
                                name='fk_stress_lease_run', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id'),
    )
    op.execute(sa.text("INSERT INTO stress_test_lease (id) VALUES (1)"))


def downgrade():
    op.drop_table('stress_test_lease')
    op.drop_index('ix_stress_test_sample_run_id', table_name='stress_test_sample')
    op.drop_table('stress_test_sample')
    op.drop_index('ix_stress_test_run_created_at', table_name='stress_test_run')
    op.drop_index('ix_stress_run_state_created', table_name='stress_test_run')
    op.drop_index('ix_stress_test_run_state', table_name='stress_test_run')
    op.drop_index('ix_stress_test_run_requested_by_id', table_name='stress_test_run')
    op.drop_index('ix_stress_test_run_uuid', table_name='stress_test_run')
    op.drop_table('stress_test_run')
    with op.batch_alter_table('role', schema=None) as batch_op:
        batch_op.drop_column('admin_performance')
