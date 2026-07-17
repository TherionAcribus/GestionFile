"""Câblage du journal d'audit des actions sensibles (point 7 — Phase 8).

Ce module fait le lien entre le noyau **pur** :pymod:`audit_log` (normalisation,
redaction) et le monde Flask/SQLAlchemy : il résout l'utilisateur courant et
l'adresse IP, écrit une ligne dans la table ``AuditLog`` et émet une trace dans
le logger applicatif.

Deux principes de robustesse :

* **Best-effort** : une défaillance de l'audit (base indisponible, etc.) ne doit
  jamais faire échouer l'action métier ni la requête HTTP. On journalise l'échec
  et on continue.
* **Transaction isolée** : l'écriture d'audit se fait dans sa propre transaction
  (``commit`` dédié). Il faut donc appeler :func:`record_audit` quand la session
  n'a plus de changement métier en attente — c'est-à-dire **après** le ``commit``
  de l'action (cas succès) ou **après** le ``rollback`` (cas échec). Ainsi un
  rollback métier n'efface pas la trace d'audit, et l'audit n'emporte pas par
  mégarde des écritures métier non committées.
"""

from __future__ import annotations

import logging

from flask import current_app, has_request_context, request

from audit_log import (
    build_audit_record,
    format_audit_record,
    OUTCOME_SUCCESS,
)

try:  # pragma: no cover - dépend de l'environnement d'exécution
    from flask_login import current_user
except Exception:  # pragma: no cover
    current_user = None


def _current_username():
    """Nom de l'utilisateur authentifié courant, ou ``None`` si indisponible.

    Tolérant hors contexte de requête / sans utilisateur connecté : on ne
    propage jamais d'exception depuis la résolution de l'identité.
    """
    if current_user is None:
        return None
    try:
        if getattr(current_user, "is_authenticated", False):
            return getattr(current_user, "username", None)
    except Exception:
        return None
    return None


def _client_ip():
    """Adresse IP source (``remote_addr``), ou ``None`` hors requête.

    On ne fait pas confiance à ``X-Forwarded-For`` (forgeable) : comme pour
    l'audit de connexion, c'est au reverse proxy de confiance de réécrire
    ``remote_addr`` s'il y en a un.
    """
    if not has_request_context():
        return None
    return request.remote_addr


def _logger():
    try:
        return current_app.logger
    except Exception:  # pragma: no cover - hors contexte d'application
        return logging.getLogger(__name__)


def persist_audit_record(session, model_cls, record: dict):
    """Écrit ``record`` comme une ligne du modèle d'audit, dans sa transaction.

    Fonction volontairement fine et sans dépendance implicite (session et modèle
    injectés) : elle est testable avec une base SQLite jouet. L'appelant est
    responsable de la gestion d'erreur ; :func:`record_audit` l'enveloppe en
    best-effort.
    """
    entry = model_cls(
        username=None if record.get("user") == "-" else record.get("user"),
        action=record.get("action"),
        resource=record.get("resource"),
        target=None if record.get("target") == "-" else record.get("target"),
        outcome=record.get("outcome"),
        ip=None if record.get("ip") == "-" else record.get("ip"),
        details=record.get("details"),
    )
    session.add(entry)
    session.commit()
    return entry


def record_audit(action, resource, *, target_id=None, outcome=OUTCOME_SUCCESS, details=None):
    """Consigne une action sensible : trace loggée + ligne ``AuditLog``.

    Résout l'utilisateur courant et l'IP, construit l'enregistrement normalisé
    (sans secret) via le noyau pur, l'émet dans le logger, puis le persiste en
    base en best-effort. Ne lève jamais : un échec d'audit est journalisé mais
    n'interrompt pas l'appelant.
    """
    record = build_audit_record(
        action,
        resource,
        user=_current_username(),
        target_id=target_id,
        outcome=outcome,
        details=details,
        ip=_client_ip(),
    )

    logger = _logger()
    # Trace toujours émise (utile même si la base est indisponible). Un succès en
    # INFO, un échec/refus en WARNING pour ressortir dans la surveillance.
    line = format_audit_record(record)
    if outcome == OUTCOME_SUCCESS:
        logger.info(line)
    else:
        logger.warning(line)

    # Persistance best-effort : jamais bloquante pour l'action métier.
    try:
        from models import db, AuditLog
        persist_audit_record(db.session, AuditLog, record)
    except Exception as exc:  # pragma: no cover - dépend de la base
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        logger.error("Audit non persisté (%s %s): %s", action, resource, exc)

    return record
