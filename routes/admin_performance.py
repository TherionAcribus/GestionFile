"""Administration des tests de charge et API interne du runner."""
from __future__ import annotations

import csv
import io
import time
import threading
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pika
from flask import (Blueprint, Response, current_app, jsonify,
                   render_template, request)
from flask_login import current_user

from audit_log import ACTION_CREATE, ACTION_UPDATE, OUTCOME_FAILURE
from audit_service import record_audit
from models import (StressTestLease, StressTestRun, StressTestSample, db)
from routes.admin_security import require_permission, require_permission_api
from stress_auth import RUNNER_SECRET_HEADER, runner_secret_valid
from stress_fixtures import (cleanup_fixtures, create_synthetic_patient,
                             prepare_fixtures)
from stress_testing import (ACTIVE_STATES, PROFILES, SCENARIOS,
                            StressValidationError, application_version,
                            compare_summaries, config_fingerprint,
                            normalize_target,
                            production_queue_is_empty, runner_status,
                            serialize_run, transition_run,
                            validate_mode, validate_run_request)

try:  # Unix/conteneur ; absent sous Windows pendant les tests locaux.
    import resource
except ImportError:  # pragma: no cover - plateforme Windows
    resource = None


admin_performance_bp = Blueprint('admin_performance', __name__)
_CPU_SAMPLE = {'at': time.monotonic(), 'cpu': time.process_time()}


def _launch_enabled(func):
    """Bloque uniquement le lancement, jamais la consultation de la page."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        if validate_mode(current_app.config.get('STRESS_TEST_MODE')) == 'disabled':
            return jsonify(error=(
                'Le mode serveur des tests de performance est désactivé. '
                'Configurez STRESS_TEST_MODE avant tout lancement.')), 409
        if not current_app.config.get('STRESS_TEST_ENABLED', False):
            return jsonify(error=(
                'Le lancement est désactivé dans Administration > Application.')), 409
        return func(*args, **kwargs)
    return wrapped


def _internal_only(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if not runner_secret_valid(request.headers.get(RUNNER_SECRET_HEADER)):
            return jsonify(error='unauthorized'), 401
        return func(*args, **kwargs)
    return wrapped


def _ready_check(target):
    if not target:
        return False
    try:
        req = Request(target + '/readyz', method='GET',
                      headers={'User-Agent': 'GestionFile-stress-preflight/1'})
        with urlopen(req, timeout=3) as response:  # nosec B310 -- URL admin fixe validee
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def _preflight(mode, target):
    status = runner_status(current_app.config.get('STRESS_RUNNER_STALE_SECONDS', 15))
    checks = {
        'runner': status['healthy'],
        'readyz': _ready_check(target),
        'no_active_run': not StressTestRun.query.filter(
            StressTestRun.state.in_(ACTIVE_STATES)).first(),
        'target_locked': bool(target),
        'production_queue_empty': True,
    }
    if mode == 'production':
        checks['production_queue_empty'] = production_queue_is_empty()
    return checks, status


@admin_performance_bp.route('/admin/performance')
@require_permission('performance')
def performance_page():
    return render_template('admin/performance.html')


@admin_performance_bp.route('/admin/performance/api/capabilities')
@require_permission_api('performance')
def capabilities():
    mode = validate_mode(current_app.config.get('STRESS_TEST_MODE'))
    try:
        target = normalize_target(current_app.config.get('STRESS_TARGET_URL'))
    except StressValidationError:
        target = ''
    checks, status = _preflight(mode, target)
    enabled = bool(current_app.config.get('STRESS_TEST_ENABLED', False))
    checks = {'feature_enabled': enabled,
              'environment_mode': mode != 'disabled', **checks}
    return jsonify(mode=mode, profiles=PROFILES, scenarios=SCENARIOS,
                   target=target, preflight=checks, runner=status,
                   enabled=enabled,
                   launch_allowed=enabled and mode != 'disabled',
                   same_host_warning=True,
                   confirmation='LANCER LE TEST')


@admin_performance_bp.route('/admin/performance/api/runs', methods=['GET'])
@require_permission_api('performance')
def list_runs():
    limit = min(max(request.args.get('limit', 30, type=int), 1), 100)
    runs = StressTestRun.query.order_by(StressTestRun.created_at.desc()).limit(limit).all()
    return jsonify(runs=[serialize_run(run) for run in runs])


@admin_performance_bp.route('/admin/performance/api/runs', methods=['POST'])
@require_permission_api('performance')
@_launch_enabled
def create_run():
    payload = request.get_json(silent=True) or {}
    mode = validate_mode(current_app.config.get('STRESS_TEST_MODE'))
    try:
        scenario, profile, requested, applied = validate_run_request(mode, payload)
        target = normalize_target(current_app.config.get('STRESS_TARGET_URL'))
    except StressValidationError as exc:
        message = str(exc)  # message metier controle, jamais une erreur technique
        record_audit(ACTION_CREATE, 'stress_test', outcome=OUTCOME_FAILURE,
                     details=message)
        return jsonify(error=message), 400
    if payload.get('confirmation') != 'LANCER LE TEST':
        return jsonify(error='Saisissez exactement LANCER LE TEST.'), 400

    # La ligne singleton serialise les lancements concurrents, meme entre deux
    # processus web. Le runner la reutilisera ensuite comme bail.
    lease = StressTestLease.query.filter_by(id=1).with_for_update().first()
    if lease is None:
        lease = StressTestLease(id=1)
        db.session.add(lease)
        db.session.flush()
    if StressTestRun.query.filter(StressTestRun.state.in_(ACTIVE_STATES)).first():
        db.session.rollback()
        return jsonify(error='Un test est deja actif.'), 409
    checks, _ = _preflight(mode, target)
    if not all(checks.values()):
        db.session.rollback()
        return jsonify(error='Le controle prealable a echoue.', preflight=checks), 409

    run = StressTestRun(
        mode=mode, scenario=scenario, profile=profile,
        requested_parameters=requested, applied_parameters=applied,
        requested_by_id=current_user.id, target_url=target,
        application_version=application_version(),
        config_fingerprint=config_fingerprint(mode, scenario, profile, applied),
        state='queued', summary={}, fixture_ids={})
    db.session.add(run)
    db.session.commit()
    record_audit(ACTION_CREATE, 'stress_test', target_id=run.uuid,
                 details=f'{mode}/{scenario}/{profile}')
    return jsonify(run=serialize_run(run, detail=True)), 201


def _get_run_or_404(run_uuid):
    return StressTestRun.query.filter_by(uuid=run_uuid).first_or_404()


@admin_performance_bp.route('/admin/performance/api/runs/<run_uuid>')
@require_permission_api('performance')
def get_run(run_uuid):
    return jsonify(run=serialize_run(_get_run_or_404(run_uuid), detail=True))


@admin_performance_bp.route('/admin/performance/api/runs/<run_uuid>/samples')
@require_permission_api('performance')
def get_samples(run_uuid):
    run = _get_run_or_404(run_uuid)
    after_id = max(request.args.get('after_id', 0, type=int), 0)
    samples = StressTestSample.query.filter(
        StressTestSample.run_id == run.id,
        StressTestSample.id > after_id).order_by(StressTestSample.id).limit(600).all()
    return jsonify(samples=[{
        'id': s.id, 'elapsed_seconds': s.elapsed_seconds,
        'active_users': s.active_users, 'requests': s.requests,
        'rps': s.rps, 'errors': s.errors, 'error_rate': s.error_rate,
        'p50_ms': s.latency_p50_ms, 'p95_ms': s.latency_p95_ms,
        'p99_ms': s.latency_p99_ms, 'max_ms': s.latency_max_ms,
        'cpu_percent': s.web_cpu_percent, 'memory_bytes': s.web_memory_bytes,
        'ready': s.ready,
    } for s in samples])


@admin_performance_bp.route('/admin/performance/api/runs/<run_uuid>/stop', methods=['POST'])
@require_permission_api('performance')
def stop_run(run_uuid):
    run = _get_run_or_404(run_uuid)
    if run.state not in ACTIVE_STATES:
        return jsonify(error='Ce test est deja termine.'), 409
    run.stop_requested = True
    if run.state == 'queued':
        transition_run(run, 'aborted', reason='user_stop', verdict='failed')
    elif run.state != 'stopping':
        transition_run(run, 'stopping', reason='user_stop')
    db.session.commit()
    record_audit(ACTION_UPDATE, 'stress_test', target_id=run.uuid,
                 details='arret demande')
    return jsonify(run=serialize_run(run))


def _csv_response(run):
    output = io.StringIO(newline='')
    # BOM + point-virgule : ouverture directe lisible dans Excel en locale
    # francaise. Le fichier contient d'abord le diagnostic humain, puis le
    # detail par endpoint et enfin la serie temporelle exploitable.
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    report = serialize_run(run, detail=True)
    summary = run.summary or {}
    writer.writerow(['RAPPORT DE TEST DE PERFORMANCE'])
    writer.writerow(['Identifiant', run.uuid])
    writer.writerow(['Scenario', report['scenario_label']])
    writer.writerow(['Profil', report['profile_label']])
    writer.writerow(['Etat', report['state_label']])
    writer.writerow(['Verdict', report['verdict_label'] or 'Non disponible'])
    writer.writerow(['Explication', report['result_message']])
    writer.writerow(['Debut', report['started_at'] or ''])
    writer.writerow(['Fin', report['finished_at'] or ''])
    writer.writerow([])
    writer.writerow(['RESUME'])
    writer.writerow(['Requetes totales', summary.get('total_requests', '')])
    writer.writerow(['Requetes reussies', summary.get('successful_requests', '')])
    writer.writerow(['Erreurs', summary.get('total_errors', '')])
    writer.writerow(["Taux d'erreur (%)", summary.get('error_rate', '')])
    writer.writerow(['Debit moyen (req/s)', summary.get('average_rps', '')])
    writer.writerow(['Debit maximal (req/s)', summary.get('max_rps', '')])
    writer.writerow(['Latence p50 (ms)', summary.get('p50_ms', '')])
    writer.writerow(['Latence p95 (ms)', summary.get('p95_ms', '')])
    writer.writerow(['Latence p99 (ms)', summary.get('p99_ms', '')])
    writer.writerow(['Latence maximale (ms)', summary.get('max_ms', '')])
    writer.writerow(['CPU maximal (%)', summary.get('max_cpu_percent', '')])
    writer.writerow(['Memoire maximale (octets)', summary.get('max_memory_bytes', '')])
    writer.writerow(['Disponibilite readyz (%)', summary.get('ready_percent', '')])
    writer.writerow([])
    writer.writerow(['RESULTATS PAR PAGE OU ACTION'])
    writer.writerow(['Methode et endpoint', 'Requetes', 'Erreurs',
                     "Taux d'erreur (%)", 'p50 (ms)', 'p95 (ms)', 'p99 (ms)',
                     'Maximum (ms)', 'Premiere erreur'])
    for name, values in (summary.get('endpoints') or {}).items():
        requests_count = values.get('requests') or 0
        errors = values.get('errors') or 0
        rate = errors / requests_count * 100 if requests_count else 0
        writer.writerow([name, requests_count, errors, rate,
                         values.get('p50_ms'), values.get('p95_ms'),
                         values.get('p99_ms'), values.get('max_ms'),
                         values.get('first_error', '')])
    writer.writerow([])
    writer.writerow(['MESURES PAR SECONDE'])
    writer.writerow(['Seconde', 'Utilisateurs', 'Requetes cumulees',
                     'Requetes/s', 'Erreurs cumulees', "Taux d'erreur (%)",
                     'p50 (ms)', 'p95 (ms)', 'p99 (ms)', 'Maximum (ms)',
                     'CPU (%)', 'Memoire (octets)', 'Application prete'])
    for s in run.samples:
        writer.writerow([s.elapsed_seconds, s.active_users, s.requests, s.rps,
                         s.errors, s.error_rate, s.latency_p50_ms,
                         s.latency_p95_ms, s.latency_p99_ms, s.latency_max_ms,
                         s.web_cpu_percent, s.web_memory_bytes,
                         'Oui' if s.ready else 'Non'])
    return Response(output.getvalue(), content_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="stress-{run.uuid}.csv"'})


@admin_performance_bp.route('/admin/performance/api/runs/<run_uuid>/export.csv')
@require_permission_api('performance')
def export_csv(run_uuid):
    return _csv_response(_get_run_or_404(run_uuid))


@admin_performance_bp.route('/admin/performance/api/runs/<run_uuid>/export.json')
@require_permission_api('performance')
def export_json(run_uuid):
    run = _get_run_or_404(run_uuid)
    return jsonify(run=serialize_run(run, detail=True), samples=[{
        'second': s.elapsed_seconds, 'users': s.active_users,
        'requests': s.requests, 'rps': s.rps, 'errors': s.errors,
        'error_rate': s.error_rate, 'p50_ms': s.latency_p50_ms,
        'p95_ms': s.latency_p95_ms, 'p99_ms': s.latency_p99_ms,
        'max_ms': s.latency_max_ms, 'cpu_percent': s.web_cpu_percent,
        'memory_bytes': s.web_memory_bytes, 'ready': s.ready,
        'endpoints': s.endpoint_summary,
    } for s in run.samples])


@admin_performance_bp.route('/admin/performance/api/compare')
@require_permission_api('performance')
def compare_runs():
    left = _get_run_or_404(request.args.get('left', ''))
    right = _get_run_or_404(request.args.get('right', ''))
    return jsonify(left=serialize_run(left), right=serialize_run(right),
                   differences=compare_summaries(left, right))


def _process_memory():
    try:
        with open('/proc/self/status', encoding='ascii') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    if resource is None:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux rapporte des KiB, macOS des octets. Le conteneur est Linux.
    return int(rss * 1024)


def _process_cpu_percent():
    now = time.monotonic()
    cpu = time.process_time()
    elapsed = now - _CPU_SAMPLE['at']
    percent = ((cpu - _CPU_SAMPLE['cpu']) / elapsed * 100) if elapsed > 0 else 0
    _CPU_SAMPLE.update(at=now, cpu=cpu)
    return max(0, percent)


def _memory_limit():
    for path in ('/sys/fs/cgroup/memory.max',
                 '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            raw = open(path, encoding='ascii').read().strip()  # noqa: SIM115
            if raw != 'max':
                value = int(raw)
                if value < 1 << 60:
                    return value
        except (OSError, ValueError):
            continue
    return None


@admin_performance_bp.route('/internal/stress/metrics')
@_internal_only
def internal_metrics():
    pool = db.engine.pool
    try:
        checked_out = pool.checkedout()
    except (AttributeError, TypeError):
        checked_out = None
    try:
        from sockets import active_connections
        socket_count = sum(len(items) for items in active_connections.values())
    except Exception:
        socket_count = None
    ready = True
    db_connections = db_activity = None
    try:
        db.session.execute(db.text('SELECT 1'))
        if db.engine.dialect.name == 'mysql':
            rows = db.session.execute(db.text(
                "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running')"))
            status = {row[0]: int(row[1]) for row in rows}
            db_connections = status.get('Threads_connected')
            db_activity = status.get('Threads_running')
    except Exception:
        ready = False
        db.session.rollback()
    if ready and current_app.config.get('START_RABBITMQ'):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(
                current_app.config.get('RABBITMQ_URL')))
            connection.close()
        except Exception:
            ready = False
    return jsonify(ready=ready, cpu_percent=_process_cpu_percent(),
                   memory_bytes=_process_memory(),
                   memory_limit_bytes=_memory_limit(),
                   threads=threading.active_count(), pool_checked_out=checked_out,
                   socket_connections=socket_count,
                   db_connections=db_connections, db_activity=db_activity)


@admin_performance_bp.route('/internal/stress/runs/<run_uuid>/fixtures', methods=['POST', 'DELETE'])
@_internal_only
def internal_fixtures(run_uuid):
    run = _get_run_or_404(run_uuid)
    if run.mode != 'staging':
        return jsonify(error='fixtures_forbidden'), 403
    if request.method == 'POST':
        return jsonify(fixtures=prepare_fixtures(run))
    cleanup_fixtures(run)
    return jsonify(cleaned=True)


@admin_performance_bp.route('/internal/stress/runs/<run_uuid>/journey', methods=['POST'])
@_internal_only
def internal_journey(run_uuid):
    run = _get_run_or_404(run_uuid)
    if run.mode != 'staging' or run.scenario not in {'kiosk', 'queue_counter', 'mixed'}:
        return jsonify(error='journey_forbidden'), 403
    action = (request.get_json(silent=True) or {}).get('action', 'create')
    if action == 'create':
        patient = create_synthetic_patient(run)
        return jsonify(patient_id=patient.id, status=patient.status), 201
    patient_id = (request.get_json(silent=True) or {}).get('patient_id')
    from models import Patient
    row = db.session.get(Patient, patient_id)
    if (row is None or not row.journey_id or
            not row.journey_id.startswith(f'stress:{run.uuid}:')):
        return jsonify(error='unknown_synthetic_patient'), 404
    states = {'call': 'calling', 'serve': 'ongoing', 'complete': 'done'}
    if action not in states:
        return jsonify(error='invalid_action'), 400
    row.status = states[action]
    db.session.commit()
    try:
        from extensions import socketio
        socketio.emit('refresh', {'source': 'stress', 'run': run.uuid})
    except Exception:
        current_app.logger.debug('Emission stress Socket.IO indisponible', exc_info=True)
    return jsonify(patient_id=row.id, status=row.status)
