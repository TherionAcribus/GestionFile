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
