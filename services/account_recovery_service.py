"""Service « récupération de compte » — opérations en base.

Partagé par les trois voies de récupération (voir ``account_recovery.py``) :
la réinitialisation par un administrateur, la commande de secours
``manage.py reset-password`` et la variable ``ADMIN_RESET_PASSWORD``.

Aucune réponse HTTP ici : les appelants traduisent le résultat (toast, sortie
console, journal).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from flask import current_app

from account_recovery import (
    ENV_RESET_VARIABLE,
    env_reset_marker,
    env_reset_needed,
    generate_temporary_password,
)
from audit_log import ACTION_CREATE, ACTION_RESET, OUTCOME_FAILURE, OUTCOME_SUCCESS
from audit_service import record_audit
from models import Role, User, db
from password_policy import validate_password


def _logger():
    try:
        return current_app.logger
    except Exception:  # pragma: no cover - hors contexte d'application
        return logging.getLogger(__name__)


def set_user_password(user, password, *, must_change):
    """Pose ``password`` sur ``user`` et révoque ses sessions (sans commit).

    Renouveler ``fs_uniquifier`` invalide les cookies de session et « se
    souvenir de moi » émis avant le changement.
    """
    user.set_password(password)
    user.fs_uniquifier = str(uuid.uuid4())
    user.must_change_password = bool(must_change)


def _admin_role():
    """Rôle ``admin`` (tout pouvoir), recréé s'il a disparu.

    À appeler AVANT de modifier le compte : la recréation du rôle valide sa
    propre transaction.
    """
    from routes.admin_security import create_default_role
    create_default_role()
    return Role.query.filter_by(name="admin").first()


def _grant_full_access(user, role):
    """Réactive le compte et lui rend le rôle ``admin``."""
    user.active = True
    if role is not None and role not in user.roles:
        user.roles.append(role)


class UnknownUserError(LookupError):
    """Le compte demandé n'existe pas (et sa création n'a pas été demandée)."""


class WeakPasswordError(ValueError):
    """Le mot de passe fourni ne respecte pas la politique."""

    def __init__(self, problems):
        super().__init__(" ".join(problems))
        self.problems = problems


def reset_password_from_cli(username, *, password=None, make_admin=False):
    """Réinitialisation de secours, depuis le terminal du conteneur.

    - ``password`` absent : un mot de passe temporaire lisible est généré ;
    - le mot de passe est toujours à changer à la prochaine connexion ;
    - ``make_admin`` : réactive le compte et lui rend le rôle ``admin`` —
      et crée le compte s'il n'existe pas (cas « plus aucun administrateur »).

    Renvoie ``(mot_de_passe, cree)``. Lève ``UnknownUserError`` ou
    ``WeakPasswordError``.
    """
    username = (username or "").strip()
    if not username:
        raise UnknownUserError(username)

    if password:
        problems = validate_password(password, username=username)
        if problems:
            raise WeakPasswordError(problems)
    else:
        password = generate_temporary_password()

    admin_role = _admin_role() if make_admin else None
    user = User.query.filter_by(username=username).first()
    created = False
    if user is None:
        if not make_admin:
            raise UnknownUserError(username)
        user = User(username=username, active=True, confirmed_at=datetime.now())
        db.session.add(user)
        created = True

    try:
        set_user_password(user, password, must_change=True)
        if make_admin:
            _grant_full_access(user, admin_role)
        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_RESET, "password", target_id=username,
                     outcome=OUTCOME_FAILURE, details="via=cli")
        raise

    record_audit(ACTION_CREATE if created else ACTION_RESET, "password",
                 target_id=user.id, outcome=OUTCOME_SUCCESS,
                 details=f"via=cli username={username} admin={bool(make_admin)}")
    return password, created


def list_accounts():
    """Comptes existants, pour guider la commande de secours."""
    return User.query.order_by(User.username).all()


def apply_env_reset(environ=None):
    """Applique ``ADMIN_RESET_PASSWORD`` au compte ``ADMIN_USERNAME`` (une fois).

    Appelée au démarrage (bootstrap, conteneur ``init``). Le compte visé est
    réactivé et reçoit le rôle ``admin`` — il est créé s'il n'existe plus. Le
    mot de passe est à changer à la connexion suivante.

    Une valeur contraire à la politique n'est PAS appliquée (erreur au
    journal) mais ne bloque pas le démarrage : la file d'attente doit rester
    disponible même si la manipulation de secours a été mal faite.

    Renvoie ``"absent"``, ``"deja_applique"``, ``"refuse"``, ``"applique"`` ou
    ``"erreur"``.
    """
    environ = os.environ if environ is None else environ
    value = (environ.get(ENV_RESET_VARIABLE) or "").strip()
    if not value:
        return "absent"

    logger = _logger()
    username = (environ.get("ADMIN_USERNAME") or "admin").strip() or "admin"
    user = User.query.filter_by(username=username).first()
    if user is not None and not env_reset_needed(value, user.env_reset_marker):
        logger.warning(
            "%s est encore définie (déjà appliquée au compte %r) : "
            "videz-la dans la configuration du déploiement.",
            ENV_RESET_VARIABLE, username)
        return "deja_applique"

    problems = validate_password(value, username=username)
    if problems:
        logger.error("%s ignorée : %s", ENV_RESET_VARIABLE, " ".join(problems))
        record_audit(ACTION_RESET, "password", target_id=username,
                     outcome=OUTCOME_FAILURE, details="via=env refus: politique")
        return "refuse"

    created = user is None
    try:
        admin_role = _admin_role()
        if created:
            user = User(username=username, active=True, confirmed_at=datetime.now())
            db.session.add(user)
        set_user_password(user, value, must_change=True)
        _grant_full_access(user, admin_role)
        user.env_reset_marker = env_reset_marker(value)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("%s : échec de la réinitialisation (%s)", ENV_RESET_VARIABLE, exc)
        record_audit(ACTION_RESET, "password", target_id=username,
                     outcome=OUTCOME_FAILURE, details="via=env")
        return "erreur"

    logger.warning(
        "Compte %r %s via %s : mot de passe à changer à la prochaine connexion. "
        "Videz maintenant cette variable dans la configuration du déploiement.",
        username, "créé" if created else "réinitialisé", ENV_RESET_VARIABLE)
    record_audit(ACTION_CREATE if created else ACTION_RESET, "password",
                 target_id=user.id, outcome=OUTCOME_SUCCESS,
                 details=f"via=env username={username}")
    return "applique"


def env_reset_still_set(environ=None):
    """Vrai si ``ADMIN_RESET_PASSWORD`` est encore renseignée (bandeau admin)."""
    environ = os.environ if environ is None else environ
    return bool((environ.get(ENV_RESET_VARIABLE) or "").strip())
