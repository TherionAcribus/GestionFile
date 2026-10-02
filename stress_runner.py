"""Worker Locust headless pilote par les demandes persistantes en base."""
from __future__ import annotations

import os
import json
import random
import socket
import threading
import time
import uuid

import requests
from locust import HttpUser, between, task
from locust.env import Environment

# Importe l'application sans bootstrap ni ordonnanceur.
os.environ.setdefault('SKIP_STARTUP_HOOKS', '1')
os.environ.setdefault('APP_ROLE', 'stress')

from app import app  # noqa: E402
from models import StressTestRun, StressTestSample, db  # noqa: E402
from stress_auth import RUNNER_SECRET_HEADER, signed_headers  # noqa: E402
from stress_fixtures import (cleanup_all_incomplete_runs, cleanup_fixtures,
                             prepare_fixtures)  # noqa: E402
from stress_testing import (claim_lease, heartbeat_lease,
                            mark_stale_runs_interrupted,
                            purge_old_runs, release_lease, StopThresholds,
                            transition_run)  # noqa: E402


RUNNER_ID = f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
SECRET = os.getenv('STRESS_RUNNER_SECRET', '')
STOP_DRAIN_SECONDS = 5


class GestionFileUser(HttpUser):
    wait_time = between(0.4, 1.6)
    abstract = False
    scenario = 'consultation'
    run_uuid = ''
    runner_secret = ''

    def _signed(self, method, path):
        return signed_headers(self.runner_secret, method, path, self.run_uuid)

    def consultation(self):
        path = random.choice(('/healthz', '/readyz', '/patient',
                              '/patient/patient_buttons', '/display',
                              '/announce/state', '/announce/patients_next'))
        self.client.get(path, name=path, headers=self._signed('GET', path))

    def journey(self):
        path = f'/internal/stress/runs/{self.run_uuid}/journey'
        headers={RUNNER_SECRET_HEADER: self.runner_secret}
        with self.client.post(path, name='/stress/journey/create', headers=headers,
                              json={'action': 'create'}, catch_response=True) as response:
            if response.status_code != 201:
                response.failure(f'creation synthetic patient: HTTP {response.status_code}')
                return
            patient_id = response.json().get('patient_id')
        for action in ('call', 'serve', 'complete'):
            self.client.post(path, name=f'/stress/journey/{action}', headers=headers,
                             json={'action': action, 'patient_id': patient_id})

    def realtime(self):
        handshake = f'/socket.io/?EIO=4&transport=polling&t={time.time_ns()}'
        with self.client.get(handshake, name='/socket.io/connect',
                             catch_response=True) as response:
            try:
                payload = response.text
                sid = json.loads(payload[payload.index('{'):])['sid']
            except (ValueError, KeyError, json.JSONDecodeError):
                response.failure('handshake Socket.IO invalide')
                return
        transport = f'/socket.io/?EIO=4&transport=polling&sid={sid}'
        self.client.post(transport, name='/socket.io/namespace-connect', data='40')
        # Le polling suivant permet de recevoir l'acquittement et les premiers
        # evenements diffuses avant une deconnexion propre.
        self.client.get(transport, name='/socket.io/receive')
        self.client.post(transport, name='/socket.io/disconnect', data='41')

    @task
    def execute_scenario(self):
        if self.scenario == 'consultation':
            self.consultation()
        elif self.scenario in {'kiosk', 'queue_counter'}:
            self.journey()
        elif self.scenario == 'realtime':
            self.realtime()
        else:
            choice = random.randint(1, 100)
            if choice <= 45:
                self.consultation()
            elif choice <= 75:
                self.journey()
            elif choice <= 95:
                self.journey()
            else:
                self.realtime()


def _internal_metrics(target):
    try:
        response = requests.get(
            target + '/internal/stress/metrics',
            headers={RUNNER_SECRET_HEADER: SECRET}, timeout=2)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return {'ready': False}


def _percentile(stats, value):
    try:
        return stats.get_response_time_percentile(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _endpoint_summary(environment):
    result = {}
    for (name, method), entry in environment.stats.entries.items():
        key = f'{method} {name}'
        result[key] = {
            'requests': entry.num_requests,
            'errors': entry.num_failures,
            'p50_ms': _percentile(entry, .50),
            'p95_ms': _percentile(entry, .95),
            'p99_ms': _percentile(entry, .99),
            'max_ms': entry.max_response_time,
        }
    # Les corps, cookies et en-tetes ne sont jamais recopies. Seulement une
    # representation bornee de la premiere erreur Locust par endpoint.
    for error in environment.stats.errors.values():
        key = f'{error.method} {error.name}'
        if key in result and 'first_error' not in result[key]:
            result[key]['first_error'] = str(error.error).replace('\n', ' ')[:180]
    return result


def _store_sample(run, environment, elapsed, metrics):
    total = environment.stats.total
    requests_count = total.num_requests
    failures = total.num_failures
    error_rate = (failures / requests_count * 100) if requests_count else 0
    reconnect_entry = environment.stats.entries.get(
        ('/socket.io/connect', 'GET'))
    sample = StressTestSample(
        run_id=run.id, elapsed_seconds=elapsed,
        active_users=environment.runner.user_count,
        requests=requests_count, rps=float(total.current_rps or 0),
        errors=failures, error_rate=error_rate,
        latency_p50_ms=_percentile(total, .50),
        latency_p95_ms=_percentile(total, .95),
        latency_p99_ms=_percentile(total, .99),
        latency_max_ms=total.max_response_time,
        web_cpu_percent=metrics.get('cpu_percent'),
        web_memory_bytes=metrics.get('memory_bytes'),
        web_memory_limit_bytes=metrics.get('memory_limit_bytes'),
        web_threads=metrics.get('threads'), ready=bool(metrics.get('ready')),
        db_connections=metrics.get('db_connections'),
        db_activity=metrics.get('db_activity'),
        pool_checked_out=metrics.get('pool_checked_out'),
        socket_connections=metrics.get('socket_connections'),
        socket_reconnections=(reconnect_entry.num_requests
                              if reconnect_entry else 0),
        endpoint_summary=_endpoint_summary(environment))
    db.session.add(sample)
    db.session.commit()
    return sample


def _final_summary(run, environment, samples):
    total = environment.stats.total
    request_count = total.num_requests
    failure_count = total.num_failures
    duration = max(1, len(samples))
    error_rate = failure_count / request_count * 100 if request_count else 0
    summary = {
        'total_requests': request_count,
        'total_errors': failure_count,
        'error_rate': error_rate,
        'average_rps': request_count / duration,
        'max_rps': max((s.rps for s in samples), default=0),
        'p50_ms': _percentile(total, .50), 'p95_ms': _percentile(total, .95),
        'p99_ms': _percentile(total, .99),
        'max_ms': total.max_response_time,
        'max_cpu_percent': max((s.web_cpu_percent or 0 for s in samples), default=0),
        'max_memory_bytes': max((s.web_memory_bytes or 0 for s in samples), default=0),
        'endpoints': _endpoint_summary(environment),
    }
    p95 = summary['p95_ms'] or 0
    if error_rate > 5 or p95 > 3000:
        verdict = 'failed'
    elif error_rate > 1 or p95 > 1500:
        verdict = 'degraded'
    else:
        verdict = 'successful'
    run.summary = summary
    run.verdict = verdict
    return verdict


def execute_run(run):
    params = run.applied_parameters
    GestionFileUser.host = run.target_url
    GestionFileUser.scenario = run.scenario
    GestionFileUser.run_uuid = run.uuid
    GestionFileUser.runner_secret = SECRET
    environment = Environment(user_classes=[GestionFileUser])
    runner = environment.create_local_runner()
    hard_stop = threading.Timer(
        int(params['duration']) + 45, lambda: os._exit(2))  # noqa: S404
    hard_stop.daemon = True
    hard_stop.start()
    samples = []
    auto_reason = None
    try:
        transition_run(run, 'preparing')
        db.session.commit()
        if run.mode == 'staging':
            prepare_fixtures(run)
        transition_run(run, 'running')
        db.session.commit()
        runner.start(user_count=int(params['users']),
                     spawn_rate=float(params['spawn_rate']))
        start = time.monotonic()
        ramp_seconds = int(params['users'] / params['spawn_rate']) + 1
        thresholds = StopThresholds()
        while time.monotonic() - start < int(params['duration']):
            tick = time.monotonic()
            db.session.expire(run)
            if run.stop_requested or run.state == 'stopping':
                auto_reason = run.stop_reason or 'user_stop'
                break
            if not heartbeat_lease(RUNNER_ID, run):
                auto_reason = 'lease_lost'
                break
            metrics = _internal_metrics(run.target_url)
            sample = _store_sample(run, environment, int(tick - start), metrics)
            samples.append(sample)
            after_ramp = tick - start > ramp_seconds
            auto_reason = thresholds.observe(
                ready=sample.ready, error_rate=sample.error_rate,
                p95_ms=sample.latency_p95_ms, after_ramp=after_ramp,
                memory_bytes=sample.web_memory_bytes,
                memory_limit_bytes=sample.web_memory_limit_bytes)
            if auto_reason:
                transition_run(run, 'stopping', reason=auto_reason)
                db.session.commit()
                break
            time.sleep(max(0, 1 - (time.monotonic() - tick)))

        runner.quit()
        deadline = time.monotonic() + STOP_DRAIN_SECONDS
        while environment.runner.user_count and time.monotonic() < deadline:
            time.sleep(.1)
        _final_summary(run, environment, samples)
        if run.state == 'stopping' or auto_reason:
            transition_run(run, 'aborted', reason=auto_reason or 'user_stop',
                           verdict='failed' if auto_reason != 'user_stop' else run.verdict)
        else:
            transition_run(run, 'completed', verdict=run.verdict)
        db.session.commit()
    except BaseException as exc:
        db.session.rollback()
        fresh = StressTestRun.query.filter_by(id=run.id).first()
        if fresh and fresh.state not in {'completed', 'failed', 'aborted', 'interrupted'}:
            try:
                transition_run(fresh, 'failed', reason='runner_error', error=exc,
                               verdict='failed')
                db.session.commit()
            except Exception:
                db.session.rollback()
        app.logger.exception('Echec du test de charge %s', run.uuid)
    finally:
        hard_stop.cancel()
        try:
            fresh = StressTestRun.query.filter_by(id=run.id).first()
            if fresh and fresh.fixture_ids:
                cleanup_fixtures(fresh)
        except Exception:
            db.session.rollback()
            app.logger.exception('Nettoyage incomplet du test %s', run.uuid)
        release_lease(RUNNER_ID)


def main():
    if len(SECRET) < 32:
        raise RuntimeError('STRESS_RUNNER_SECRET absent ou trop court.')
    last_purge = 0.0
    with app.app_context():
        stale = mark_stale_runs_interrupted(
            app.config.get('STRESS_RUNNER_STALE_SECONDS', 15))
        for run in stale:
            if run.fixture_ids:
                cleanup_fixtures(run)
        cleanup_all_incomplete_runs()
        while True:
            try:
                if not claim_lease(RUNNER_ID, None):
                    time.sleep(1)
                    continue
                run = StressTestRun.query.filter_by(state='queued').order_by(
                    StressTestRun.created_at).first()
                if run and claim_lease(RUNNER_ID, run):
                    execute_run(run)
                elif time.monotonic() - last_purge > 86400:
                    purge_old_runs(app.config.get('STRESS_RESULTS_RETENTION_DAYS', 30))
                    last_purge = time.monotonic()
            except Exception:
                db.session.rollback()
                app.logger.exception('Boucle stress-runner en erreur')
            time.sleep(1)


if __name__ == '__main__':
    main()
