"""Authentification interne et signature des requetes synthetiques."""
from __future__ import annotations

import hashlib
import hmac
import time

from flask import abort, current_app, g, request


RUNNER_SECRET_HEADER = 'X-Stress-Runner-Secret'
RUN_HEADER = 'X-Stress-Run'
TIMESTAMP_HEADER = 'X-Stress-Timestamp'
SIGNATURE_HEADER = 'X-Stress-Signature'

# Seules ces lectures peuvent utiliser l'identite interne du runner. Les
# actions metier, l'administration et les autres routes restent soumises a
# leur authentification habituelle.
CONSULTATION_PATHS = frozenset({
    '/patient',
    '/patient/patient_buttons',
    '/display',
    '/announce/state',
    '/announce/patients_next',
})

# Verifier la persistence a chaque requete fausserait principalement la mesure
# du pool SQL. Ce cache tres court conserve le controle "test encore actif"
# tout en limitant cette requete de securite a environ une fois par seconde et
# par processus web.
_ACTIVE_RUN_CACHE: dict[str, tuple[float, bool]] = {}
_ACTIVE_RUN_CACHE_SECONDS = 1.0


def runner_secret_valid(value: str | None) -> bool:
    expected = current_app.config.get('STRESS_RUNNER_SECRET') or ''
    return bool(expected and value and hmac.compare_digest(expected, value))


def signature_for(secret: str, method: str, path: str, run_uuid: str,
                  timestamp: str) -> str:
    message = f'{method.upper()}\n{path}\n{run_uuid}\n{timestamp}'.encode('utf-8')
    return hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()


def signed_headers(secret: str, method: str, path: str, run_uuid: str) -> dict:
    timestamp = str(int(time.time()))
    return {
        RUN_HEADER: run_uuid,
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: signature_for(secret, method, path, run_uuid, timestamp),
    }


def identify_stress_request(max_age_seconds=30) -> None:
    """Marque g.stress_test_request uniquement pour une signature valide.

    Hook ``before_request`` : il ne doit JAMAIS retourner de valeur — Flask
    traiterait tout retour non ``None`` (même ``False``) comme la réponse de
    la requête, provoquant un TypeError/500 systématique. Il se contente de
    peupler ``g`` ou d'``abort(401)`` sur une signature invalide.
    """
    run_uuid = request.headers.get(RUN_HEADER)
    timestamp = request.headers.get(TIMESTAMP_HEADER)
    supplied = request.headers.get(SIGNATURE_HEADER)
    if not any((run_uuid, timestamp, supplied)):
        g.stress_test_request = False
        return
    secret = current_app.config.get('STRESS_RUNNER_SECRET') or ''
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= max_age_seconds
    except (TypeError, ValueError):
        fresh = False
    expected = signature_for(secret, request.method, request.path,
                             run_uuid or '', timestamp or '') if secret else ''
    valid = bool(secret and run_uuid and supplied and fresh and
                 hmac.compare_digest(expected, supplied))
    if not valid:
        abort(401)
    g.stress_test_request = valid
    g.stress_test_run_uuid = run_uuid


def is_stress_request() -> bool:
    return bool(getattr(g, 'stress_test_request', False))


def is_authorized_stress_consultation() -> bool:
    """Autorise une lecture publique precise pendant un test actif.

    Une signature valide ne devient jamais une session generale : l'UUID doit
    correspondre a un test ``consultation`` ou ``mixed`` en cours, son mode
    doit correspondre au mode du serveur, et le chemin/methode sont limites a
    la liste blanche ci-dessus.
    """
    if (not is_stress_request() or request.method != 'GET' or
            request.path not in CONSULTATION_PATHS):
        return False

    run_uuid = getattr(g, 'stress_test_run_uuid', '')
    now = time.monotonic()
    cached = _ACTIVE_RUN_CACHE.get(run_uuid)
    if cached and cached[0] > now:
        return cached[1]

    # Import local pour eviter un cycle stress_auth -> models -> application.
    from models import StressTestRun

    row = StressTestRun.query.with_entities(
        StressTestRun.mode, StressTestRun.scenario, StressTestRun.state,
    ).filter_by(uuid=run_uuid).first()
    configured_mode = str(current_app.config.get('STRESS_TEST_MODE', 'disabled')).lower()
    allowed = bool(
        row and row.state in {'preparing', 'running', 'stopping'} and
        row.scenario in {'consultation', 'mixed'} and
        row.mode == configured_mode and
        (row.mode != 'production' or row.scenario == 'consultation')
    )
    _ACTIVE_RUN_CACHE[run_uuid] = (now + _ACTIVE_RUN_CACHE_SECONDS, allowed)
    return allowed
