"""Contrats purs et persistence des tests de performance."""
from datetime import timedelta

import pytest
from flask import Flask

from models import StressTestLease, StressTestRun, User, db
from stress_auth import (identify_stress_request,
                         is_authorized_stress_consultation, signed_headers,
                         signature_for)
from stress_testing import (StressValidationError, claim_lease,
                            compare_summaries, heartbeat_lease, now_local,
                            StopThresholds, transition_run,
                            validate_run_request)


@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      STRESS_TEST_MODE='staging',
                      STRESS_RUNNER_SECRET='s' * 40)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        user = User(username='admin', password='x', fs_uniquifier='stress-user', active=True)
        db.session.add_all([user, StressTestLease(id=1)])
        db.session.commit()
    return app


def _run(user_id, **overrides):
    values = dict(mode='staging', scenario='consultation', profile='light',
                  requested_parameters={}, applied_parameters={
                      'users': 5, 'spawn_rate': 1, 'duration': 60},
                  requested_by_id=user_id, target_url='http://web:5000',
                  config_fingerprint='0' * 64, state='queued', summary={}, fixture_ids={})
    values.update(overrides)
    return StressTestRun(**values)


def test_profiles_and_production_are_strictly_validated():
    scenario, profile, _, applied = validate_run_request(
        'production', {'scenario': 'consultation', 'profile': 'production_check'})
    assert (scenario, profile) == ('consultation', 'production_check')
    assert applied == {'users': 3, 'spawn_rate': 1, 'duration': 30,
                       'estimated_requests': 41}
    with pytest.raises(StressValidationError):
        validate_run_request('production', {'scenario': 'kiosk',
                                             'profile': 'production_check'})
    with pytest.raises(StressValidationError):
        validate_run_request('staging', {'scenario': 'consultation', 'profile': 'custom',
                                         'users': 501, 'spawn_rate': 1, 'duration': 10})


def test_state_machine_rejects_reverse_and_terminal_transitions(app):
    with app.app_context():
        user = User.query.one()
        run = _run(user.id)
        db.session.add(run)
        db.session.commit()
        transition_run(run, 'preparing')
        transition_run(run, 'running')
        transition_run(run, 'completed', verdict='successful')
        assert run.finished_at is not None
        with pytest.raises(StressValidationError):
            transition_run(run, 'running')


def test_singleton_lease_cannot_be_stolen_before_expiry(app):
    with app.app_context():
        run = _run(User.query.one().id)
        db.session.add(run)
        db.session.commit()
        assert claim_lease('runner-a', run, ttl_seconds=30)
        assert not claim_lease('runner-b', run, ttl_seconds=30)
        assert heartbeat_lease('runner-a', run)
        lease = db.session.get(StressTestLease, 1)
        lease.expires_at = now_local() - timedelta(seconds=1)
        db.session.commit()
        assert claim_lease('runner-b', run, ttl_seconds=30)


def test_comparison_has_absolute_and_percentage_delta(app):
    with app.app_context():
        uid = User.query.one().id
        left = _run(uid, summary={'average_rps': 10, 'p95_ms': 100})
        right = _run(uid, summary={'average_rps': 12, 'p95_ms': 80})
        result = compare_summaries(left, right)
        assert result['average_rps']['absolute'] == 2
        assert result['average_rps']['percent'] == 20
        assert result['p95_ms']['percent'] == -20


def test_automatic_stop_thresholds_require_consecutive_seconds():
    threshold = StopThresholds()
    for _ in range(2):
        assert threshold.observe(ready=False, error_rate=0, p95_ms=10,
                                 after_ramp=True) is None
    assert threshold.observe(ready=True, error_rate=0, p95_ms=10,
                             after_ramp=True) is None
    for _ in range(9):
        assert threshold.observe(ready=True, error_rate=5.1, p95_ms=10,
                                 after_ramp=True) is None
    assert threshold.observe(ready=True, error_rate=5.1, p95_ms=10,
                             after_ramp=True) == 'error_rate_threshold'


def test_memory_threshold_uses_container_limit():
    threshold = StopThresholds()
    assert threshold.observe(ready=True, error_rate=0, p95_ms=1,
                             after_ramp=True, memory_bytes=86,
                             memory_limit_bytes=100) == 'memory_threshold'


def test_stress_signature_binds_method_path_run_and_time():
    secret = 's' * 40
    signature = signature_for(secret, 'POST', '/patient', 'run-1', '123')
    assert signature == signature_for(secret, 'POST', '/patient', 'run-1', '123')
    assert signature != signature_for(secret, 'GET', '/patient', 'run-1', '123')


def test_signed_consultation_only_opens_allowlisted_reads_for_active_run(app):
    with app.app_context():
        run = _run(User.query.one().id, state='running')
        db.session.add(run)
        db.session.commit()
        headers = signed_headers(app.config['STRESS_RUNNER_SECRET'], 'GET',
                                 '/patient', run.uuid)
        with app.test_request_context('/patient', headers=headers):
            identify_stress_request()
            assert is_authorized_stress_consultation()

        admin_headers = signed_headers(app.config['STRESS_RUNNER_SECRET'], 'GET',
                                       '/admin', run.uuid)
        with app.test_request_context('/admin', headers=admin_headers):
            identify_stress_request()
            assert not is_authorized_stress_consultation()

        finished = _run(User.query.one().id, state='completed',
                        verdict='successful')
        db.session.add(finished)
        db.session.commit()
        finished_headers = signed_headers(
            app.config['STRESS_RUNNER_SECRET'], 'GET', '/display', finished.uuid)
        with app.test_request_context('/display', headers=finished_headers):
            identify_stress_request()
            assert not is_authorized_stress_consultation()


def test_human_csv_report_contains_diagnosis_and_endpoint_details(app):
    from routes.admin_performance import _csv_response

    with app.app_context():
        run = _run(User.query.one().id, state='aborted', verdict='failed',
                   stop_reason='error_rate_threshold', summary={
                       'total_requests': 20, 'successful_requests': 10,
                       'total_errors': 10, 'error_rate': 50,
                       'endpoints': {'GET /patient': {
                           'requests': 10, 'errors': 10, 'p95_ms': 25,
                           'first_error': 'HTTP 401'}},
                   })
        db.session.add(run)
        db.session.commit()
        text = _csv_response(run).get_data(as_text=True)
        assert text.startswith('\ufeffRAPPORT DE TEST DE PERFORMANCE')
        assert "Plus de 5 % d'erreurs pendant 10 secondes" in text
        assert 'RESULTATS PAR PAGE OU ACTION' in text
        assert 'GET /patient;10;10;100.0' in text
