"""« Mot de passe oublié » par e-mail (account_recovery.py).

Deux pages publiques (hors /admin) :

- ``/forgot_password`` : la personne saisit son identifiant ou son adresse.
  La réponse est TOUJOURS la même, que le compte existe ou non, et l'envoi part
  en tâche de fond pour que le temps de réponse ne le trahisse pas non plus
  (même principe anti-énumération que la connexion). Demandes limitées par IP
  et par identité ;
- ``/reset_password/<jeton>`` : lien reçu par e-mail, signé, valable une heure
  et à usage unique (le jeton porte une empreinte du mot de passe courant).

Le lien pointe vers l'adresse publique enregistrée dans Application › E-mails
(``MAIL_LINK_BASE_URL``), jamais vers l'en-tête Host de la requête anonyme :
sinon un attaquant pourrait faire envoyer à la victime un lien vers son propre
site (empoisonnement de lien).

Sans envoi d'e-mails configuré (ou option désactivée), ``/forgot_password``
affiche simplement les autres façons de retrouver l'accès.
"""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from sqlalchemy import func

from account_recovery import (
    make_reset_token,
    normalize_base_url,
    read_reset_token,
    reset_email_content,
    reset_link,
    token_matches_password,
)
from audit_log import ACTION_RESET, ACTION_UPDATE, OUTCOME_DENIED, OUTCOME_FAILURE, OUTCOME_SUCCESS
from audit_service import record_audit
from extensions import socketio
from login_guard import LoginThrottle
from mail_settings import is_configured as mail_is_configured
from mail_settings import is_valid_email
from services.mail_service import env_brevo_api_key
from models import User, db
from password_policy import validate_password
from services.account_recovery_service import set_user_password
from services.mail_service import send_email

password_reset_bp = Blueprint("password_reset", __name__)

# Demandes de lien : 3 libres par quart d'heure et par clé (IP, identité),
# puis délai croissant. En mémoire par process, comme la connexion.
_request_throttle = LoginThrottle(free_attempts=3, base_delay=60.0,
                                  max_delay=900.0, window=900.0)

GENERIC_SENT_MESSAGE = (
    "Si un compte correspond et possède une adresse e-mail, un message vient d'y être "
    "envoyé. Pensez à vérifier le dossier « indésirables »."
)


def reset_by_email_available(config=None):
    """Vrai si l'option est active ET l'envoi d'e-mails configuré."""
    config = config or current_app.config
    return (bool(config.get("MAIL_PASSWORD_RESET", True))
            and mail_is_configured(config, env_brevo_api_key()))


def _run_async(func_, *args):
    """Tâche de fond (eventlet ou threads) ; remplacée en test par un appel direct."""
    socketio.start_background_task(func_, *args)


def _send_reset_email(app, to, subject, body):
    with app.app_context():
        ok, error = send_email(to, subject, body)
        if not ok:
            app.logger.warning("E-mail de réinitialisation non envoyé : %s", error)


def _find_account(identifier):
    """Compte actif désigné par son identifiant ou son adresse e-mail."""
    user = User.query.filter_by(username=identifier).first()
    if user is None and "@" in identifier:
        user = User.query.filter(func.lower(User.email) == identifier.lower()).first()
    if user is None or not user.active or not is_valid_email(user.email or ""):
        return None
    return user


@password_reset_bp.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    available = reset_by_email_available()
    if request.method == "GET" or not available:
        return render_template("security/forgot_password.html", available=available, sent=False)

    identifier = (request.form.get("identifier") or "").strip()[:255]
    if not identifier:
        return render_template("security/forgot_password.html", available=True, sent=False,
                               error="Indiquez votre identifiant ou votre adresse e-mail.")

    ip_key = f"reset-ip:{request.remote_addr or 'unknown'}"
    id_key = f"reset-id:{identifier.lower()}"
    # Chaque demande compte (envoyée ou non) ; au-delà des demandes libres,
    # plus rien n'est envoyé jusqu'à la fin du délai — réponse inchangée.
    ip_delay = _request_throttle.register_failure(ip_key)
    id_delay = _request_throttle.register_failure(id_key)
    if ip_delay > 0 or id_delay > 0:
        record_audit(ACTION_RESET, "password", target_id=identifier,
                     outcome=OUTCOME_DENIED, details="via=email demande limitée")
    else:
        user = _find_account(identifier)
        if user is not None:
            base_url = (normalize_base_url(current_app.config.get("MAIL_LINK_BASE_URL"))
                        or normalize_base_url(request.host_url))
            token = make_reset_token(current_app.config["SECRET_KEY"], user.id, user.password)
            link = reset_link(base_url, url_for("password_reset.reset_password", token=token))
            site = current_app.config.get("PHARMACY_NAME") or "PharmaFile"
            subject, body = reset_email_content(user.username, site, link)
            _run_async(_send_reset_email, current_app._get_current_object(),
                       user.email, subject, body)
            record_audit(ACTION_RESET, "password", target_id=user.id,
                         outcome=OUTCOME_SUCCESS, details="via=email lien envoyé")
        else:
            record_audit(ACTION_RESET, "password", target_id=identifier,
                         outcome=OUTCOME_FAILURE, details="via=email aucun compte joignable")

    return render_template("security/forgot_password.html", available=True, sent=True)


def _account_for_token(token):
    data = read_reset_token(current_app.config["SECRET_KEY"], token)
    if data is None:
        return None
    user = db.session.get(User, data[0])
    if user is None or not user.active or not token_matches_password(data[1], user.password):
        return None
    return user


def _no_referrer(response, status=200):
    """Le jeton est dans l'URL : ne jamais le transmettre en Referer."""
    response = current_app.make_response((response, status))
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@password_reset_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = _account_for_token(token)
    if user is None:
        return _no_referrer(render_template("security/reset_password.html", invalid=True), 400)

    errors = []
    if request.method == "POST":
        password1 = request.form.get("password1") or ""
        password2 = request.form.get("password2") or ""
        if password1 != password2:
            errors.append("Les deux mots de passe ne correspondent pas.")
        errors.extend(validate_password(password1, username=user.username))
        if not errors:
            try:
                set_user_password(user, password1, must_change=False)
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Échec de la réinitialisation par e-mail")
                errors.append("Le changement a échoué, réessayez.")
            else:
                record_audit(ACTION_UPDATE, "password", target_id=user.id,
                             outcome=OUTCOME_SUCCESS,
                             details=f"via=email username={user.username}")
                return redirect(url_for("admin_security.login", reset="done"))

    return _no_referrer(
        render_template("security/reset_password.html", invalid=False,
                        username=user.username, errors=errors),
        400 if errors else 200)
