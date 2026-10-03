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
STATE_LABELS = {
    'queued': 'En attente', 'preparing': 'Préparation', 'running': 'En cours',
    'stopping': 'Arrêt en cours', 'completed': 'Terminé', 'failed': 'Échec',
    'aborted': 'Arrêté', 'interrupted': 'Interrompu',
}
VERDICT_LABELS = {
    'successful': 'Réussi', 'degraded': 'Dégradé', 'failed': 'Échoué',
}
STOP_REASON_LABELS = {
    'user_stop': "Arrêt demandé par l'utilisateur",
    'error_rate_threshold': "Plus de 5 % d'erreurs pendant 10 secondes",
    'p95_threshold': 'Temps de réponse p95 supérieur à 3 secondes pendant 15 secondes',
    'readiness_failed': "Application indisponible lors de trois contrôles consécutifs",
    'memory_threshold': 'Mémoire du serveur supérieure à 85 % de la limite',
    'runner_heartbeat_lost': 'Connexion avec le runner perdue',
    'lease_lost': "Le runner a perdu l'autorisation exclusive d'exécuter le test",
    'runner_error': 'Erreur interne du runner',
}
STATE_TRANSITIONS = {
    'queued': {'preparing', 'aborted', 'interrupted'},
    'preparing': {'running', 'stopping', 'failed', 'aborted', 'interrupted'},
    'running': {'stopping', 'completed', 'failed', 'aborted', 'interrupted'},
    'stopping': {'completed', 'failed', 'aborted', 'interrupted'},
}


def run_result_message(run):
    """Explication courte, directement affichable, du resultat d'un test."""
    if run.stop_reason:
        return STOP_REASON_LABELS.get(run.stop_reason, run.stop_reason)
    if run.state == 'completed':
        if run.verdict == 'successful':
            return 'Le test est allé au terme prévu sans dépasser les seuils.'
        if run.verdict == 'degraded':
            return 'Le test est allé au terme prévu, mais les performances sont dégradées.'
        return 'Le test est allé au terme prévu, mais les seuils de qualité sont dépassés.'
    if run.state in ACTIVE_STATES:
        return 'Le test est en cours. Les résultats sont encore provisoires.'
    return "Aucune explication supplémentaire n'est disponible."


def business_interpretation(run, reference_patients=10):
    """Traduit les métriques techniques en lecture métier prudente.

    Le nombre d'utilisateurs Locust est une charge simultanée, pas une mesure
    exacte du nombre de patients que la pharmacie peut accueillir. Le rapport
    parle donc toujours de capacité *validée* au niveau testé, jamais de
    capacité maximale extrapolée depuis les RPS.
    """
    try:
        reference = min(500, max(1, int(reference_patients)))
    except (TypeError, ValueError):
        reference = 10
    try:
        users = max(0, int((run.applied_parameters or {}).get('users', 0)))
    except (TypeError, ValueError):
        users = 0

    load_names = {
        'consultation': 'visiteurs simulés en parallèle',
        'kiosk': 'parcours borne actifs en parallèle',
        'queue_counter': 'prises en charge actives en parallèle',
        'realtime': 'connexions temps réel simulées en parallèle',
        'mixed': 'utilisateurs simulés en parallèle',
    }
    load_name = load_names.get(run.scenario, 'utilisateurs simulés en parallèle')
    load_label = f'{users} {load_name}' if users else 'Charge simultanée inconnue'
    margin = users / reference if users else None
    margin_text = (f'{margin:.1f}'.rstrip('0').rstrip('.').replace('.', ',')
                   if margin is not None else None)
    margin_label = (f'{margin_text} fois le pic habituel'
                    if margin_text is not None else 'Non calculable')

    summary = run.summary or {}
    p95 = summary.get('p95_ms')
    if not isinstance(p95, (int, float)):
        response_label, response_detail = 'Non mesurée', 'Latence indisponible'
    elif p95 <= 100:
        response_label, response_detail = 'Excellente', f'95 % des réponses en moins de {p95:.0f} ms'
    elif p95 <= 300:
        response_label, response_detail = 'Très bonne', f'95 % des réponses en moins de {p95:.0f} ms'
    elif p95 <= 500:
        response_label, response_detail = 'Bonne', f'95 % des réponses en moins de {p95:.0f} ms'
    elif p95 <= 1000:
        response_label, response_detail = 'Acceptable', f'95 % des réponses en moins de {p95:.0f} ms'
    else:
        response_label, response_detail = 'Lente', f'95 % des réponses en moins de {p95:.0f} ms'

    endpoints = summary.get('endpoints') or {}
    completed_journeys = None
    journeys_per_minute = None
    if run.scenario in {'kiosk', 'queue_counter', 'mixed'}:
        complete = endpoints.get('POST /stress/journey/complete') or {}
        completed_journeys = max(
            0, int(complete.get('requests') or 0) - int(complete.get('errors') or 0))
        duration = summary.get('duration_seconds')
        if isinstance(duration, (int, float)) and duration > 0:
            journeys_per_minute = completed_journeys / duration * 60

    partial = run.state == 'aborted' and run.stop_reason == 'user_stop'
    if run.state in ACTIVE_STATES:
        grade, tone = 'Test en cours', 'info'
        headline = 'Les résultats sont encore provisoires.'
        conclusion = 'Attendez la fin du test avant d’évaluer la capacité.'
    elif partial:
        grade, tone = 'Résultat partiel', 'warning'
        headline = "Aucun problème majeur détecté avant l'arrêt manuel."
        conclusion = "La charge n'est pas considérée comme validée car le test n'est pas allé à son terme."
    elif run.state == 'completed' and run.verdict == 'successful':
        tone = 'success'
        if margin is not None and margin >= 5:
            grade = 'Marge très importante'
        elif margin is not None and margin >= 2:
            grade = 'Marge confortable'
        elif margin is not None and margin >= 1:
            grade = 'Charge habituelle validée'
        else:
            grade = 'Test réussi, charge trop légère'
        headline = f'Le serveur a validé au moins {load_label}.'
        conclusion = 'Le test est terminé sans dépasser les seuils de performance.'
        if margin is not None:
            conclusion += (f' La charge testée représente {margin_text} fois votre '
                           f'pic habituel de {reference} patients.')
    elif run.verdict == 'degraded':
        grade, tone = 'Performances à surveiller', 'warning'
        headline = f'La charge de {load_label} a été traitée avec des ralentissements.'
        conclusion = 'Consultez les erreurs et la latence avant de valider ce niveau de charge.'
    else:
        grade, tone = 'Limite du test dépassée', 'danger'
        headline = f'La charge de {load_label} a dépassé un seuil de sécurité.'
        conclusion = run_result_message(run)
        if margin is not None and margin > 1:
            conclusion += (f' Cette charge extrême représente {margin_text} fois '
                           f'votre pic habituel; '
                           f'elle ne remet pas en cause à elle seule un pic habituel de {reference} patients.')

    if run.scenario == 'realtime':
        caveat = ('Les connexions temps réel ne correspondent pas directement à des patients : '
                  'la comparaison donne seulement un ordre de grandeur de la marge.')
    else:
        caveat = ('Un utilisateur virtuel répète ses actions en boucle : cette comparaison est '
                  'un indicateur prudent, pas une capacité maximale garantie.')

    return {
        'grade': grade, 'tone': tone, 'headline': headline,
        'conclusion': conclusion, 'tested_users': users,
        'load_label': load_label, 'reference_patients': reference,
        'margin_factor': margin, 'margin_label': margin_label,
        'response_label': response_label, 'response_detail': response_detail,
        'completed_journeys': completed_journeys,
        'journeys_per_minute': journeys_per_minute,
        'capacity_validated': bool(
            run.state == 'completed' and run.verdict == 'successful'),
        'partial': partial, 'caveat': caveat,
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


def serialize_run(run: StressTestRun, *, detail=False, reference_patients=None):
    data = {
        'uuid': run.uuid, 'mode': run.mode, 'scenario': run.scenario,
        'scenario_label': SCENARIOS.get(run.scenario, {}).get('label', run.scenario),
        'profile': run.profile,
        'profile_label': PROFILES.get(run.profile, {}).get('label', run.profile),
        'requested_parameters': run.requested_parameters,
        'applied_parameters': run.applied_parameters,
        'state': run.state, 'verdict': run.verdict,
        'state_label': STATE_LABELS.get(run.state, run.state),
        'verdict_label': VERDICT_LABELS.get(run.verdict, run.verdict),
        'stop_reason': run.stop_reason, 'stop_requested': run.stop_requested,
        'stop_reason_label': STOP_REASON_LABELS.get(run.stop_reason, run.stop_reason),
        'result_message': run_result_message(run),
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
    if reference_patients is not None:
        data['business_interpretation'] = business_interpretation(
            run, reference_patients)
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
