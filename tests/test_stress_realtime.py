"""Fiabilite du temps reel pendant un test de charge (sondes + ecouteurs)."""
import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import StressTestLease, StressTestRun, User, db
from stress_realtime import (PROBE_NAMESPACE, ListenerRecord, analyze_realtime,
                             build_probe, realtime_message)
from stress_testing import business_interpretation, run_result_message

RUN = 'run-uuid'
SECRET = 's' * 40


def _envelope(probe):
    # Enveloppe diffusee par communication_websocket.
    return {'flag': None, 'data': probe, 'revision': None}


def _web(pid, action, now=100.0):
    return _envelope(build_probe(RUN, 'web', patient_id=pid, action=action, now=now))


def _sched(seq, now=100.0):
    return _envelope(build_probe(RUN, 'scheduler', seq=seq, now=now))


def _listener(web=(), seqs=(), connected=True, disconnects=0):
    record = ListenerRecord()
    if connected:
        record.on_connect()
    for pid, action in web:
        record.on_probe(_web(pid, action), RUN, received_at=100.05)
    for seq in seqs:
        record.on_probe(_sched(seq), RUN, received_at=100.2)
    record.disconnects = disconnects
    return record


EXPECTED = {(1, 'create'), (1, 'call'), (2, 'create')}


# --- Ecouteur --------------------------------------------------------------

def test_listener_ignores_other_runs_and_malformed_probes():
    record = ListenerRecord()
    assert record.on_probe(_web(1, 'call'), RUN, received_at=100.1) is True
    assert record.on_probe(_envelope(build_probe('other', 'web', patient_id=1,
                                                 action='call')), RUN) is False
    assert record.on_probe(_web(1, 'drop_table'), RUN) is False
    assert record.on_probe(_envelope({'run': RUN, 'origin': 'web'}), RUN) is False
    assert record.on_probe(_envelope({'run': RUN, 'origin': 'x'}), RUN) is False
    assert record.on_probe('not-a-dict', RUN) is False
    assert record.web == {(1, 'call'): 1}
    assert record.latencies_ms == [pytest.approx(100.0)]


def test_voluntary_close_is_not_counted_as_a_disconnect():
    record = ListenerRecord()
    record.on_disconnect()
    record.closing = True
    record.on_disconnect('client disconnect')
    assert record.disconnects == 1


# --- Analyse ---------------------------------------------------------------

def test_every_probe_received_by_every_listener_is_ok():
    listeners = [_listener(EXPECTED, seqs=(1, 2, 3)) for _ in range(3)]
    result = analyze_realtime(EXPECTED, listeners, scheduler_sent=3, relay='rabbitmq')
    assert result['status'] == 'ok'
    assert result['web']['deliveries_expected'] == 9
    assert result['web']['lost'] == 0
    assert result['scheduler']['status'] == 'ok'
    assert result['latency_ms']['p95'] == pytest.approx(200.0)


def test_missed_web_events_are_counted_per_listener():
    listeners = [_listener(EXPECTED), _listener({(1, 'create')}, disconnects=1)]
    result = analyze_realtime(EXPECTED, listeners)
    assert result['status'] == 'lost'
    assert result['web']['lost'] == 2
    assert result['web']['worst_listener_lost'] == 2
    assert result['web']['loss_percent'] == pytest.approx(2 / 6 * 100)
    assert result['disconnects'] == 1
    assert result['scheduler']['status'] == 'not_measured'


def test_duplicates_and_unconfirmed_events_are_reported_not_lost():
    record = _listener(EXPECTED | {(9, 'call')})
    record.on_probe(_web(1, 'call'), RUN)
    result = analyze_realtime(EXPECTED, [record])
    assert result['status'] == 'ok'
    assert (result['web']['duplicates'], result['web']['unexpected']) == (1, 1)


def test_scheduler_without_relay_never_reaches_clients():
    result = analyze_realtime(EXPECTED, [_listener(EXPECTED), _listener(EXPECTED)],
                              scheduler_sent=30, relay='none')
    assert result['status'] == 'lost'
    assert result['scheduler']['status'] == 'unreachable'
    assert result['scheduler']['lost'] == 60
    tone, text = realtime_message(result)
    assert tone == 'danger' and 'Activez RabbitMQ' in text


def test_scheduler_unreachable_despite_rabbitmq_points_to_its_config():
    result = analyze_realtime(set(), [_listener()], scheduler_sent=5, relay='rabbitmq')
    tone, text = realtime_message(result)
    assert tone == 'danger' and 'malgré RabbitMQ' in text


def test_scheduler_partial_and_later_probes_ignored():
    # La sonde 4 a ete emise apres la lecture du compteur : non attendue.
    result = analyze_realtime(set(), [_listener(seqs=(1, 3, 4))], scheduler_sent=3)
    assert result['scheduler']['status'] == 'partial'
    assert (result['scheduler']['received_max'], result['scheduler']['lost']) == (2, 1)


def test_silent_scheduler_is_a_warning():
    result = analyze_realtime(EXPECTED, [_listener(EXPECTED)], scheduler_sent=0)
    assert result['status'] == 'warning'
    tone, text = realtime_message(result)
    assert tone == 'warning' and "n'a émis aucune sonde" in text


def test_no_connected_listener_means_unmeasured_not_lost():
    result = analyze_realtime(EXPECTED, [_listener(connected=False)], scheduler_sent=4)
    assert result['status'] == 'unavailable'
    assert result['web']['lost'] == 0
    assert realtime_message(result)[0] == 'warning'


def test_ok_message_lists_sources_and_delay():
    result = analyze_realtime(EXPECTED, [_listener(EXPECTED, seqs=(1, 2))],
                              scheduler_sent=2)
    tone, text = realtime_message(result)
    assert tone == 'success'
    assert '3 du serveur web, 2 du planificateur' in text
    assert 'délai p95' in text
    assert realtime_message(None) == (None, None)


# --- Lecture metier --------------------------------------------------------

def _run(**overrides):
    values = dict(mode='staging', scenario='queue_counter', profile='light',
                  requested_parameters={}, applied_parameters={
                      'users': 25, 'spawn_rate': 1, 'duration': 60},
                  requested_by_id=1, target_url='http://web:5000',
                  config_fingerprint='0' * 64, state='completed',
                  verdict='degraded', summary={'p95_ms': 80}, fixture_ids={})
    values.update(overrides)
    return StressTestRun(**values)


def test_lost_realtime_reads_as_realtime_issue_not_slowness():
    realtime = analyze_realtime(EXPECTED, [_listener()], scheduler_sent=3, relay='none')
    run = _run(summary={'p95_ms': 80, 'realtime': realtime})
    result = business_interpretation(run, 10)
    assert result['grade'] == 'Temps réel à fiabiliser'
    assert result['realtime_tone'] == 'danger'
    assert 'temps réel' in run_result_message(run)


# --- Serveur : namespace, sonde web, sonde planificateur ------------------------

@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='t',
                      SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      STRESS_TEST_MODE='staging', STRESS_RUNNER_SECRET=SECRET)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(username='admin', password='x', fs_uniquifier='rt', active=True),
            StressTestLease(id=1)])
        db.session.commit()
        yield app


def test_probe_namespace_requires_runner_secret_and_staging(app):
    from extensions import socketio
    import sockets  # noqa: F401 -- enregistre les handlers
    from stress_auth import RUNNER_SECRET_HEADER
    socketio.init_app(app)
    good = socketio.test_client(app, namespace=PROBE_NAMESPACE,
                                headers={RUNNER_SECRET_HEADER: SECRET})
    assert good.is_connected(PROBE_NAMESPACE)
    good.disconnect(namespace=PROBE_NAMESPACE)
    anonymous = socketio.test_client(app, namespace=PROBE_NAMESPACE)
    assert not anonymous.is_connected(PROBE_NAMESPACE)
    app.config['STRESS_TEST_MODE'] = 'production'
    production = socketio.test_client(app, namespace=PROBE_NAMESPACE,
                                      headers={RUNNER_SECRET_HEADER: SECRET})
    assert not production.is_connected(PROBE_NAMESPACE)


def _stored_run(**overrides):
    run = _run(state='running', verdict=None, summary={}, **overrides)
    db.session.add(run)
    db.session.commit()
    return run


def test_journey_actions_emit_a_probe_before_answering(app, monkeypatch):
    import communication
    from routes.admin_performance import admin_performance_bp
    from stress_auth import RUNNER_SECRET_HEADER
    from stress_fixtures import prepare_fixtures
    sent = []
    monkeypatch.setattr(communication, 'communication_websocket',
                        lambda stream, data=None, **kw: sent.append((stream, data, kw)))
    app.register_blueprint(admin_performance_bp)
    run = _stored_run()
    prepare_fixtures(run)
    client = app.test_client()
    path = f'/internal/stress/runs/{run.uuid}/journey'
    headers = {RUNNER_SECRET_HEADER: SECRET}
    created = client.post(path, json={'action': 'create'}, headers=headers)
    assert created.status_code == 201
    patient_id = created.get_json()['patient_id']
    assert client.post(path, json={'action': 'call', 'patient_id': patient_id},
                       headers=headers).status_code == 200
    assert [(stream, data['origin'], data['patient_id'], data['action'], kw['event'])
            for stream, data, kw in sent] == [
        ('socket_stress', 'web', patient_id, 'create', 'probe'),
        ('socket_stress', 'web', patient_id, 'call', 'probe')]
    assert all(data['run'] == run.uuid for _, data, _ in sent)


def test_scheduler_probe_counts_its_emissions_in_database(app, monkeypatch):
    import scheduler_functions
    sent = []
    monkeypatch.setattr(scheduler_functions, 'communication_websocket',
                        lambda stream, data=None, **kw: sent.append(data))
    assert scheduler_functions.emit_stress_probe() is None  # aucun test en cours
    run = _stored_run()
    assert scheduler_functions.emit_stress_probe() == 1
    assert scheduler_functions.emit_stress_probe() == 2
    db.session.expire_all()
    assert db.session.get(StressTestRun, run.id).scheduler_probes_sent == 2
    assert [(probe['origin'], probe['seq']) for probe in sent] == [
        ('scheduler', 1), ('scheduler', 2)]
    run.state = 'stopping'
    db.session.commit()
    assert scheduler_functions.emit_stress_probe() is None
    assert len(sent) == 2


def test_scheduler_probe_ignores_non_probe_scenarios(app, monkeypatch):
    import scheduler_functions
    monkeypatch.setattr(scheduler_functions, 'communication_websocket',
                        lambda *a, **kw: pytest.fail('aucune sonde attendue'))
    _stored_run(scenario='consultation')
    assert scheduler_functions.emit_stress_probe() is None


def test_probe_job_is_volatile_and_staging_only(app):
    import scheduler_functions as sf
    from extensions import scheduler
    app.config['STRESS_TEST_MODE'] = 'production'
    assert sf.ensure_stress_probe_job(app) == 'disabled'
    app.config['STRESS_TEST_MODE'] = 'staging'
    try:
        assert sf.ensure_stress_probe_job(app) == 'added'
        assert sf.ensure_stress_probe_job(app) == 'added'  # idempotent
        job = scheduler.get_job(sf.STRESS_PROBE_JOB_ID, sf.STRESS_PROBE_JOBSTORE)
        assert job is not None
        assert job.trigger.interval.total_seconds() == 1
    finally:
        try:
            scheduler.remove_job(sf.STRESS_PROBE_JOB_ID, sf.STRESS_PROBE_JOBSTORE)
        except Exception:
            pass
        try:
            scheduler.remove_jobstore(sf.STRESS_PROBE_JOBSTORE)
        except Exception:
            pass


# --- Bout en bout runner (necessite Locust/Gevent, image stress-runner) -------

def _has_websocket_client():
    try:
        import websocket  # noqa: F401
    except ImportError:
        return False
    return True


def test_runner_listeners_measure_real_socketio_delivery(monkeypatch):
    """Vrais ecouteurs du runner + vrais utilisateurs Locust contre un vrai
    serveur Flask-SocketIO qui « oublie » d'emettre la sonde « call » d'un
    patient sur quatre : chaque ecouteur doit compter exactement ces trous."""
    stress_runner = pytest.importorskip('stress_runner')
    gevent = pytest.importorskip('gevent')
    import threading
    from types import SimpleNamespace

    from flask import request
    from flask_socketio import SocketIO
    from locust.env import Environment
    from werkzeug.serving import make_server

    from stress_auth import RUNNER_SECRET_HEADER

    fake = Flask('fake_realtime_server')
    sio = SocketIO(fake, async_mode='threading')
    counter, lock = {'next': 0}, threading.Lock()

    @sio.on('connect', namespace=PROBE_NAMESPACE)
    def _connect():
        return request.headers.get(RUNNER_SECRET_HEADER) == SECRET

    @fake.post('/internal/stress/runs/<run_uuid>/journey')
    def journey(run_uuid):
        body = request.get_json()
        action = body['action']
        if action == 'create':
            with lock:
                counter['next'] += 1
                patient_id = counter['next']
        else:
            patient_id = body['patient_id']
        if not (action == 'call' and patient_id % 4 == 0):
            sio.emit('probe', _envelope(build_probe(
                run_uuid, 'web', patient_id=patient_id, action=action)),
                namespace=PROBE_NAMESPACE)
        status = {'create': 'standing', 'call': 'calling', 'serve': 'ongoing',
                  'complete': 'done'}[action]
        return {'patient_id': patient_id, 'status': status}, (201 if action == 'create' else 200)

    server = make_server('127.0.0.1', 0, fake, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(stress_runner, 'SECRET', SECRET)
    user = stress_runner.GestionFileUser
    monkeypatch.setattr(user, 'scenario', 'queue_counter')
    monkeypatch.setattr(user, 'run_uuid', RUN)
    monkeypatch.setattr(user, 'runner_secret', SECRET)
    monkeypatch.setattr(user, 'wait_time', lambda self: 0.05)
    stress_runner.LEDGER.reset()
    run = SimpleNamespace(mode='staging', scenario='queue_counter', uuid=RUN,
                          target_url=target)
    listeners = stress_runner._start_listeners(run)
    environment = Environment(user_classes=[user], host=target)
    runner = environment.create_local_runner()
    try:
        assert all(listener.record.connected for listener in listeners), [
            listener.record.connect_error for listener in listeners]
        runner.start(3, spawn_rate=3)
        gevent.sleep(2)
        runner.quit()
        gevent.sleep(1)
        transports = {listener.client.transport() for listener in listeners}
        stress_runner._stop_listeners(listeners)
        result = analyze_realtime(stress_runner.LEDGER.confirmed_actions,
                                  [listener.record for listener in listeners],
                                  relay='none')
        dropped = [key for key in stress_runner.LEDGER.confirmed_actions
                   if key[1] == 'call' and key[0] % 4 == 0]
        assert result['listeners_connected'] == stress_runner.REALTIME_LISTENERS
        assert result['web']['expected'] >= 12
        assert dropped, 'le scenario doit produire au moins un trou'
        assert result['web']['lost'] == len(dropped) * len(listeners)
        assert result['web']['duplicates'] == 0
        assert result['disconnects'] == 0
        assert result['latency_ms']['samples'] > 0
        assert transports == {'websocket'} or not _has_websocket_client()
    finally:
        runner.quit()
        stress_runner._stop_listeners(listeners)
        # Sous gevent, shutdown() du serveur de dev peut attendre indefiniment
        # apres des sessions WebSocket : arret borne, le thread est demon.
        gevent.spawn(server.shutdown).join(timeout=3)
        server.server_close()


def test_comparison_includes_realtime_only_when_measured():
    from stress_testing import compare_summaries
    without = _run(summary={'average_rps': 10, 'realtime': analyze_realtime(
        EXPECTED, [_listener({(1, 'create')})], scheduler_sent=4, relay='none')})
    with_relay = _run(summary={'average_rps': 10, 'realtime': analyze_realtime(
        EXPECTED, [_listener(EXPECTED, seqs=(1, 2, 3, 4))], scheduler_sent=4,
        relay='rabbitmq')})
    result = compare_summaries(without, with_relay)
    assert result['scheduler_probes_lost'] == {
        'left': 4, 'right': 0, 'absolute': -4, 'percent': -100}
    assert result['realtime_loss_percent']['right'] == 0
    plain = compare_summaries(_run(summary={'average_rps': 1}),
                              _run(summary={'average_rps': 2}))
    assert 'realtime_loss_percent' not in plain
