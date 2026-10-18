"""Bilan de fiabilite des donnees en fin de test de charge."""
import pytest
from flask import Flask

from models import Activity, Patient, StressTestLease, StressTestRun, User, db
from stress_reconciliation import (SAMPLE_LIMIT, JourneyLedger, reconcile,
                                   reconciliation_message)
from stress_testing import business_interpretation, run_result_message


# --- Noyau pur -------------------------------------------------------------

def test_every_confirmed_patient_in_expected_state_is_ok():
    result = reconcile({1: 'done', 2: 'calling'}, {1: 'done', 2: 'calling'})
    assert result['status'] == 'ok'
    assert result['consistent'] == 2
    assert result['lost'] == 0


def test_confirmed_patient_missing_from_database_is_a_loss():
    result = reconcile({1: 'done', 2: 'standing'}, {1: 'done'})
    assert result['status'] == 'lost'
    assert (result['missing'], result['lost']) == (1, 1)
    assert result['samples']['missing'] == [2]


def test_patient_back_to_earlier_state_is_a_lost_update():
    result = reconcile({1: 'done'}, {1: 'calling'})
    assert result['status'] == 'lost'
    assert result['regressed'] == 1
    assert result['samples']['regressed'] == [
        {'id': 1, 'expected': 'done', 'stored': 'calling'}]


def test_unknown_stored_status_counts_as_regression():
    assert reconcile({1: 'standing'}, {1: 'weird'})['regressed'] == 1


def test_server_ahead_of_last_ack_is_ambiguous_not_lost():
    # L'action « complete » a ete commitee mais sa reponse n'est jamais
    # arrivee (arret du test) : pas une perte.
    result = reconcile({1: 'ongoing'}, {1: 'done'})
    assert result['status'] == 'ambiguous'
    assert (result['ahead'], result['lost']) == (1, 0)


def test_creation_applied_without_response_is_ambiguous_not_lost():
    result = reconcile({1: 'done'}, {1: 'done', 2: 'standing'},
                       create_failures=1)
    assert result['status'] == 'ambiguous'
    assert result['unacknowledged'] == 1
    assert result['create_failures'] == 1


def test_loss_wins_over_ambiguity_and_samples_are_bounded():
    acknowledged = {i: 'done' for i in range(50)}
    stored = {i: 'standing' for i in range(30)}
    stored[999] = 'done'
    result = reconcile(acknowledged, stored)
    assert result['status'] == 'lost'
    assert (result['missing'], result['regressed']) == (20, 30)
    assert len(result['samples']['missing']) == SAMPLE_LIMIT
    assert len(result['samples']['regressed']) == SAMPLE_LIMIT


def test_json_round_trip_keys_are_accepted():
    # Les cles deviennent des chaines si le registre transite par du JSON.
    assert reconcile({'7': 'done'}, {7: 'done'})['status'] == 'ok'


def test_ledger_keeps_last_confirmed_state_and_ignores_garbage():
    ledger = JourneyLedger()
    ledger.created(5, None)
    assert ledger.acknowledged == {5: 'standing'}
    ledger.acknowledge(5, 'calling')
    ledger.acknowledge(5, 'bogus')
    ledger.acknowledge(None, 'done')
    ledger.create_failed()
    assert ledger.acknowledged == {5: 'calling'}
    assert ledger.create_failures == 1
    ledger.reset()
    assert (ledger.acknowledged, ledger.create_failures) == ({}, 0)


def test_messages_carry_tone_and_counts():
    assert reconciliation_message(None) == (None, None)
    tone, text = reconciliation_message(reconcile({1: 'done', 2: 'done'},
                                                  {1: 'done', 2: 'done'}))
    assert tone == 'success' and 'les 2 patients' in text
    tone, text = reconciliation_message(reconcile({1: 'done'}, {}))
    assert tone == 'danger' and '1 absent(s)' in text
    tone, text = reconciliation_message(reconcile({1: 'calling'}, {1: 'done'}))
    assert tone == 'warning' and 'sans que la réponse' in text
    tone, _ = reconciliation_message(reconcile({}, {}))
    assert tone == 'warning'


# --- Lecture metier --------------------------------------------------------

def _run(**overrides):
    values = dict(mode='staging', scenario='queue_counter', profile='light',
                  requested_parameters={}, applied_parameters={
                      'users': 25, 'spawn_rate': 1, 'duration': 60},
                  requested_by_id=1, target_url='http://web:5000',
                  config_fingerprint='0' * 64, state='completed',
                  verdict='successful', summary={'p95_ms': 80,
                                                 'duration_seconds': 60},
                  fixture_ids={})
    values.update(overrides)
    return StressTestRun(**values)


def test_intact_data_keeps_the_capacity_reading():
    run = _run()
    run.summary['reconciliation'] = reconcile({1: 'done'}, {1: 'done'})
    result = business_interpretation(run, 10)
    assert result['capacity_validated'] is True
    assert result['reliability_tone'] == 'success'
    assert result['grade'] == 'Marge confortable'


def test_data_loss_overrides_even_a_manual_partial_stop():
    run = _run(state='aborted', stop_reason='user_stop', verdict='failed')
    run.summary['reconciliation'] = reconcile({1: 'done'}, {})
    result = business_interpretation(run, 10)
    assert result['grade'] == 'Données perdues'
    assert result['tone'] == 'danger'
    assert result['capacity_validated'] is False
    assert 'perdu' in run_result_message(_run(
        state='completed', verdict='failed',
        summary={'reconciliation': reconcile({1: 'done'}, {})}))


def test_run_without_journeys_has_no_reliability_line():
    result = business_interpretation(_run(scenario='consultation'), 10)
    assert result['reliability_detail'] is None


# --- Runner (necessite Locust/Gevent, image stress-runner) ------------------

@pytest.fixture()
def sqlite_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(username='admin', password='x', fs_uniquifier='rec', active=True),
            StressTestLease(id=1)])
        db.session.commit()
        yield app


def test_runner_reconciles_ledger_against_tagged_rows_only(sqlite_app):
    stress_runner = pytest.importorskip('stress_runner')
    run = _run()
    activity = Activity(name='STRESS-X', letter='Z', notification=False)
    db.session.add_all([run, activity])
    db.session.flush()
    rows = [Patient(call_number=f'S{i:03d}', activity_id=activity.id,
                    status=status, journey_id=f'stress:{run.uuid}:{i:012d}')
            for i, status in enumerate(('done', 'calling', 'standing'))]
    # Patient reel (ou d'un autre test) : jamais compte dans le bilan.
    rows.append(Patient(call_number='A001', activity_id=activity.id,
                        status='standing', journey_id='borne-journey'))
    db.session.add_all(rows)
    db.session.commit()
    stress_runner.LEDGER.reset()
    stress_runner.LEDGER.created(rows[0].id)
    stress_runner.LEDGER.acknowledge(rows[0].id, 'done')
    stress_runner.LEDGER.created(rows[1].id)
    stress_runner.LEDGER.acknowledge(rows[1].id, 'done')   # regressed
    stress_runner.LEDGER.created(rows[2].id)                # consistent
    stress_runner.LEDGER.created(424242)                    # missing
    result = stress_runner._reconcile_journeys(run)
    assert result['stored_patients'] == 3
    assert (result['consistent'], result['regressed'], result['missing']) == (2, 1, 1)
    assert result['status'] == 'lost'
    run.scenario = 'consultation'
    assert stress_runner._reconcile_journeys(run) is None


def test_locust_journey_detects_a_server_that_acks_then_drops_writes():
    """Bout en bout cote runner : vrais utilisateurs Locust contre un faux
    serveur qui confirme « complete » sans l'enregistrer pour 1 patient sur 3,
    et refuse « serve » (HTTP 500) pour 1 patient sur 5."""
    stress_runner = pytest.importorskip('stress_runner')
    gevent = pytest.importorskip('gevent')
    import threading

    from locust.env import Environment
    from werkzeug.serving import make_server

    store, lock = {}, threading.Lock()
    fake = Flask('fake_server')

    @fake.post('/internal/stress/runs/<run_uuid>/journey')
    def journey(run_uuid):
        from flask import request
        body = request.get_json()
        with lock:
            if body['action'] == 'create':
                patient_id = len(store) + 1
                store[patient_id] = 'standing'
                return {'patient_id': patient_id, 'status': 'standing'}, 201
            patient_id = body['patient_id']
            status = {'call': 'calling', 'serve': 'ongoing',
                      'complete': 'done'}[body['action']]
            if body['action'] == 'serve' and patient_id % 5 == 0:
                return {'error': 'boom'}, 500
            if not (body['action'] == 'complete' and patient_id % 3 == 0):
                store[patient_id] = status
            return {'patient_id': patient_id, 'status': status}

    server = make_server('127.0.0.1', 0, fake, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    user = stress_runner.GestionFileUser
    saved = (user.scenario, user.run_uuid, user.runner_secret, user.wait_time)
    user.scenario, user.run_uuid, user.runner_secret = 'queue_counter', 'run', 'x'
    user.wait_time = lambda self: 0.05
    stress_runner.LEDGER.reset()
    environment = Environment(user_classes=[user],
                              host=f'http://127.0.0.1:{server.server_port}')
    runner = environment.create_local_runner()
    try:
        runner.start(3, spawn_rate=3)
        gevent.sleep(2)
        runner.quit()
        with lock:
            stored = dict(store)
        acknowledged = dict(stress_runner.LEDGER.acknowledged)
        result = reconcile(acknowledged, stored)
        dropped = [pid for pid, status in acknowledged.items()
                   if status == 'done' and pid % 3 == 0]
        assert len(acknowledged) >= 6
        assert dropped, 'le scenario doit produire au moins une perte'
        assert result['regressed'] == len(dropped)
        assert result['missing'] == 0
        assert result['status'] == 'lost'
        # Un refus (500) n'est jamais compte comme confirme.
        assert all(acknowledged[pid] != 'ongoing'
                   for pid in acknowledged if pid % 5 == 0)
    finally:
        runner.quit()
        server.shutdown()
        thread.join(timeout=2)
        (user.scenario, user.run_uuid, user.runner_secret, user.wait_time) = saved
