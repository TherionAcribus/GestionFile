"""Regles partagees par l'administration et le runner de tests de charge."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_

from config import time_tz
from models import db, Patient, StressTestLease, StressTestRun, StressTestSample


ACTIVE_STATES = frozenset({'queued', 'preparing', 'running', 'stopping'})
TERMINAL_STATES = frozenset({'completed', 'failed', 'aborted', 'interrupted'})
STATE_TRANSITIONS = {
    'queued': {'preparing', 'aborted', 'interrupted'},
    'preparing': {'running', 'stopping', 'failed', 'aborted', 'interrupted'},
    'running': {'stopping', 'completed', 'failed', 'aborted', 'interrupted'},
    'stopping': {'completed', 'failed', 'aborted', 'interrupted'},
}

PROFILES = {
    'production_check': {'label': 'Verification production', 'users': 3,
                         'spawn_rate': 1, 'duration': 30,
                         'modes': ('production',)},
    'light': {'label': 'Leger', 'users': 5, 'spawn_rate': 1, 'duration': 60,
              'modes': ('staging',)},
    'normal': {'label': 'Normal', 'users': 25, 'spawn_rate': 5, 'duration': 180,
               'modes': ('staging',)},
    'sustained': {'label': 'Soutenu', 'users': 100, 'spawn_rate': 10,
                  'duration': 300, 'modes': ('staging',)},
    'limit': {'label': 'Limite', 'users': 250, 'spawn_rate': 25,
              'duration': 600, 'modes': ('staging',)},
    'custom': {'label': 'Personnalise', 'modes': ('staging',)},
}

SCENARIOS = {
    'consultation': {'label': 'Consultation', 'read_only': True},
    'kiosk': {'label': 'Parcours borne', 'read_only': False},
    'queue_counter': {'label': 'File et comptoir', 'read_only': False},
    'realtime': {'label': 'Temps reel', 'read_only': True},
    'mixed': {'label': 'Mixte recommande', 'read_only': False,
              'weights': {'consultation': 45, 'kiosk': 30,
                          'queue_counter': 20, 'realtime': 5}},
}


class StressValidationError(ValueError):
    pass


def now_local():
    # Colonnes DateTime existantes sans timezone : valeur locale naive stable.
    return datetime.now(time_tz).replace(tzinfo=None)


def normalize_target(value: str) -> str:
    parsed = urlsplit((value or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise StressValidationError('STRESS_TARGET_URL est absent ou invalide.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StressValidationError('STRESS_TARGET_URL ne doit contenir ni identifiants ni parametres.')
    path = parsed.path.rstrip('/')
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, '', ''))


def validate_mode(mode: str) -> str:
    mode = (mode or 'disabled').strip().lower()
    if mode not in {'disabled', 'staging', 'production'}:
        return 'disabled'
    return mode


def validate_run_request(mode: str, payload: dict) -> tuple[str, str, dict, dict]:
    mode = validate_mode(mode)
    if mode == 'disabled':
        raise StressValidationError('Les tests de performance sont desactives.')
    scenario = str(payload.get('scenario') or '').strip()
    profile = str(payload.get('profile') or '').strip()
    if scenario not in SCENARIOS:
        raise StressValidationError('Scenario inconnu.')
    if profile not in PROFILES:
        raise StressValidationError('Profil inconnu.')
    if mode not in PROFILES[profile]['modes']:
        raise StressValidationError('Ce profil n est pas autorise dans le mode courant.')
    if mode == 'production' and scenario != 'consultation':
        raise StressValidationError('En production, seule la consultation est autorisee.')

    requested = {'scenario': scenario, 'profile': profile}
    if profile == 'custom':
        try:
            users = int(payload.get('users'))
            spawn_rate = float(payload.get('spawn_rate'))
            duration = int(payload.get('duration'))
        except (TypeError, ValueError):
            raise StressValidationError('Les parametres personnalises doivent etre numeriques.')
        if not 1 <= users <= 500:
            raise StressValidationError('Le nombre d utilisateurs doit etre compris entre 1 et 500.')
        if not 1 <= spawn_rate <= 50:
            raise StressValidationError('La montee doit etre comprise entre 1 et 50 utilisateurs/s.')
        if not 10 <= duration <= 900:
            raise StressValidationError('La duree doit etre comprise entre 10 et 900 secondes.')
        requested.update(users=users, spawn_rate=spawn_rate, duration=duration)
        applied = {'users': users, 'spawn_rate': spawn_rate, 'duration': duration}
    else:
        spec = PROFILES[profile]
        applied = {key: spec[key] for key in ('users', 'spawn_rate', 'duration')}
    applied['estimated_requests'] = estimate_requests(scenario, applied)
    return scenario, profile, requested, applied


def estimate_requests(scenario: str, params: dict) -> int:
    per_user_second = {
        'consultation': 0.45, 'kiosk': 0.22, 'queue_counter': 0.30,
        'realtime': 0.08, 'mixed': 0.34,
    }[scenario]
    return int(math.ceil(params['users'] * params['duration'] * per_user_second))


def config_fingerprint(mode: str, scenario: str, profile: str, applied: dict) -> str:
    safe = {'mode': mode, 'scenario': scenario, 'profile': profile,
            'applied': applied}
    raw = json.dumps(safe, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def transition_run(run: StressTestRun, new_state: str, *, reason=None,
                   error=None, verdict=None, at=None):
    if new_state not in STATE_TRANSITIONS.get(run.state, set()):
        raise StressValidationError(
            f'Transition interdite : {run.state} vers {new_state}.')
    at = at or now_local()
    run.state = new_state
    if new_state == 'preparing' and run.started_at is None:
        run.started_at = at
    if new_state == 'stopping' and run.stopping_at is None:
        run.stopping_at = at
    if new_state in TERMINAL_STATES:
        run.finished_at = at
    if reason:
        run.stop_reason = str(reason)[:80]
    if error:
        run.error_message = str(error).replace('\r', ' ').replace('\n', ' ')[:500]
    if verdict:
        run.verdict = verdict
    return run


def active_run_query():
    return StressTestRun.query.filter(StressTestRun.state.in_(ACTIVE_STATES))


def runner_status(stale_seconds=15):
    lease = db.session.get(StressTestLease, 1)
    now = now_local()
    healthy = bool(lease and lease.heartbeat_at and
                   lease.heartbeat_at >= now - timedelta(seconds=stale_seconds))
    return {
        'healthy': healthy,
        'runner_id': lease.runner_id if lease else None,
        'heartbeat_at': lease.heartbeat_at.isoformat() if lease and lease.heartbeat_at else None,
        'active_run_uuid': lease.run.uuid if lease and lease.run else None,
    }


def claim_lease(runner_id: str, run: StressTestRun | None, ttl_seconds=5) -> bool:
    now = now_local()
    lease = StressTestLease.query.filter_by(id=1).with_for_update().first()
    if lease is None:
        lease = StressTestLease(id=1)
        db.session.add(lease)
        db.session.flush()
    if (lease.runner_id and lease.runner_id != runner_id and lease.expires_at
            and lease.expires_at > now):
        return False
    lease.runner_id = runner_id
    lease.run = run
    lease.heartbeat_at = now
    lease.expires_at = now + timedelta(seconds=ttl_seconds)
    if run is not None:
        run.runner_heartbeat_at = now
    db.session.commit()
    return True


def heartbeat_lease(runner_id: str, run: StressTestRun | None, ttl_seconds=5) -> bool:
    now = now_local()
    updated = StressTestLease.query.filter_by(id=1, runner_id=runner_id).update({
        StressTestLease.run_id: run.id if run else None,
        StressTestLease.heartbeat_at: now,
        StressTestLease.expires_at: now + timedelta(seconds=ttl_seconds),
    }, synchronize_session=False)
    if run is not None:
        run.runner_heartbeat_at = now
    db.session.commit()
    return updated == 1


def release_lease(runner_id: str):
    lease = StressTestLease.query.filter_by(id=1, runner_id=runner_id).first()
    if lease:
        lease.run_id = None
        lease.expires_at = now_local() + timedelta(seconds=5)
        lease.heartbeat_at = now_local()
        db.session.commit()


def mark_stale_runs_interrupted(stale_seconds=15):
    cutoff = now_local() - timedelta(seconds=stale_seconds)
    runs = StressTestRun.query.filter(
        StressTestRun.state.in_({'preparing', 'running', 'stopping'}),
        or_(StressTestRun.runner_heartbeat_at.is_(None),
            StressTestRun.runner_heartbeat_at < cutoff)).all()
    for run in runs:
        transition_run(run, 'interrupted', reason='runner_heartbeat_lost',
                       verdict='failed')
    if runs:
        db.session.commit()
    return runs


def purge_old_runs(retention_days=30):
    cutoff = now_local() - timedelta(days=retention_days)
    deleted = StressTestRun.query.filter(
        StressTestRun.state.in_(TERMINAL_STATES),
        StressTestRun.finished_at < cutoff).delete(synchronize_session=False)
    db.session.commit()
    return deleted


def serialize_run(run: StressTestRun, *, detail=False):
    data = {
        'uuid': run.uuid, 'mode': run.mode, 'scenario': run.scenario,
        'scenario_label': SCENARIOS.get(run.scenario, {}).get('label', run.scenario),
        'profile': run.profile,
        'profile_label': PROFILES.get(run.profile, {}).get('label', run.profile),
        'requested_parameters': run.requested_parameters,
        'applied_parameters': run.applied_parameters,
        'state': run.state, 'verdict': run.verdict,
        'stop_reason': run.stop_reason, 'stop_requested': run.stop_requested,
        'created_at': run.created_at.isoformat() if run.created_at else None,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'runner_heartbeat_at': (run.runner_heartbeat_at.isoformat()
                                if run.runner_heartbeat_at else None),
        'summary': run.summary or {}, 'error_message': run.error_message,
        'requested_by': run.requested_by.username if run.requested_by else None,
    }
    if detail:
        data.update(target_url=run.target_url,
                    application_version=run.application_version,
                    config_fingerprint=run.config_fingerprint)
    return data


def compare_summaries(left: StressTestRun, right: StressTestRun):
    metrics = ('average_rps', 'p95_ms', 'p99_ms', 'error_rate',
               'max_cpu_percent', 'max_memory_bytes')
    result = {}
    for metric in metrics:
        lv = (left.summary or {}).get(metric)
        rv = (right.summary or {}).get(metric)
        absolute = rv - lv if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) else None
        percent = (absolute / lv * 100) if absolute is not None and lv else None
        result[metric] = {'left': lv, 'right': rv, 'absolute': absolute,
                          'percent': percent}
    return result


def production_queue_is_empty():
    return not db.session.query(Patient.id).filter(
        Patient.status.in_({'standing', 'calling', 'ongoing'})).first()


def application_version():
    return (os.getenv('APP_VERSION') or os.getenv('SOURCE_COMMIT') or
            os.getenv('GIT_COMMIT') or 'unknown')[:80]


class StopThresholds:
    """Compteurs consecutifs des seuils d'arret automatique."""

    def __init__(self):
        self.readiness_failures = 0
        self.error_seconds = 0
        self.latency_seconds = 0

    def observe(self, *, ready, error_rate, p95_ms, after_ramp,
                memory_bytes=None, memory_limit_bytes=None):
        self.readiness_failures = self.readiness_failures + 1 if not ready else 0
        self.error_seconds = self.error_seconds + 1 if after_ramp and error_rate > 5 else 0
        self.latency_seconds = self.latency_seconds + 1 if after_ramp and (p95_ms or 0) > 3000 else 0
        if self.readiness_failures >= 3:
            return 'readiness_failed'
        if self.error_seconds >= 10:
            return 'error_rate_threshold'
        if self.latency_seconds >= 15:
            return 'p95_threshold'
        if (memory_bytes and memory_limit_bytes and
                memory_bytes > memory_limit_bytes * .85):
            return 'memory_threshold'
        return None
