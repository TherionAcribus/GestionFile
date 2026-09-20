from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, has_request_context, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

try:
    from flask_security import current_user
except Exception:  # pragma: no cover
    current_user = None


# Valeurs qui ne doivent JAMAIS être acceptées comme un vrai secret partagé :
# chaîne vide et placeholders bien connus (cf. .env.example). Un secret laissé à
# l'une de ces valeurs équivaut à « non configuré ».
_WEAK_APP_SECRETS = {"", "changez_moi", "change_me", "changeme", "secret", "password"}


def is_valid_app_secret_config(secret) -> bool:
    """True si APP_SECRET est configuré avec une vraie valeur.

    Refuse une valeur absente/None, vide, uniquement des espaces, ou un
    placeholder connu. Sert à la fois à la validation de démarrage (le serveur
    refuse de démarrer sans secret) et à la défense en profondeur dans
    l'émission de token (ne jamais authentifier sur un secret vide)."""
    if not secret:
        return False
    return str(secret).strip().lower() not in _WEAK_APP_SECRETS


def check_app_secret(provided, configured) -> bool:
    """Vérifie le secret fourni par un client face au secret serveur configuré.

    - Refuse toujours si le secret serveur n'est pas réellement configuré
      (empêche qu'un APP_SECRET absent/vide accepte un secret vide côté client).
    - Refuse un secret fourni vide.
    - Comparaison en temps constant pour éviter les attaques temporelles."""
    if not is_valid_app_secret_config(configured):
        return False
    if not provided:
        return False
    return hmac.compare_digest(str(provided), str(configured))


def generate_app_token(duree_jours: int = 1) -> str:
    """Emet un jeton applicatif signe, valable ``duree_jours`` jours.

    Vit ici, aux cotes de son pendant ``verify_app_token``, plutot que dans
    app.py (point 9.5d).
    """
    expiration = datetime.now(timezone.utc) + timedelta(days=duree_jours)
    return jwt.encode({"exp": expiration}, current_app.config["SECRET_KEY"], algorithm="HS256")


def verify_app_token(token: str) -> bool:
    if not token:
        return False
    try:
        jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


# Validité du jeton téléphone-patient : alignée sur le max_age (30 min) des
# cookies patient_id / patient_call_number posés par /patient/phone/ping.
_PATIENT_PHONE_TOKEN_MAX_AGE = 30 * 60


def make_patient_phone_token(patient_id, call_number) -> str:
    """Jeton signé liant le couple (patient_id, call_number) d'un téléphone.

    Posé en cookie ``patient_token`` au moment où le serveur pose les cookies
    patient. Les cookies patient restent lisibles côté client (le JS s'en sert
    pour filtrer 'your_turn' et pour le rattrapage au reconnect) : le jeton
    prouve seulement que CES valeurs ont bien été émises par le serveur.
    """
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps({"pid": str(patient_id), "call": str(call_number)},
                   salt="patient-phone")


def check_patient_phone_token(token, patient_id, call_number) -> bool:
    """Vérifie le jeton téléphone face aux valeurs des cookies patient.

    Sans lui, poser ``patient_call_number=X`` suffisait à rejoindre la salle
    ``call_X`` de ``/socket_phone`` et à lire le statut d'un autre patient via
    ``/patient/phone/status``. Comparaison en temps constant pour ne pas
    donner d'oracle sur la valeur du jeton.
    """
    if not token or not patient_id or not call_number:
        return False
    try:
        data = URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(
            token, salt="patient-phone", max_age=_PATIENT_PHONE_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return hmac.compare_digest(
        f"{data.get('pid')}:{data.get('call')}",
        f"{patient_id}:{call_number}")


def is_authenticated_request() -> bool:
    if not has_request_context():
        return False

    token = request.headers.get("X-App-Token")
    if token and verify_app_token(token):
        return True

    if current_user is None:
        return False

    return bool(getattr(current_user, "is_authenticated", False))


def is_admin_session() -> bool:
    """Vérifie qu'une **session admin authentifiée** est active.

    Contrairement à ``is_authenticated_request``, cette fonction **n'accepte
    pas** un jeton applicatif (``X-App-Token``) : un jeton machine générique
    ne prouve pas que le client est une interface d'administration.

    Utilisée pour le namespace Socket.IO ``/socket_admin`` (point 2 — audit
    Admin) : seuls les utilisateurs connectés via l'interface web admin
    peuvent rejoindre ce namespace et recevoir ses évènements (toasts,
    rafraîchissements de tableaux, etc.).

    La vérification de **permission** (l'utilisateur a-t-il au moins une
    permission admin ?) est laissée à l'appelant, car elle dépend du modèle
    ``Role`` et du registre de permissions — des dépendances que ce module
    garde volontairement légères.
    """
    if not has_request_context():
        return False

    # Pas de jeton applicatif : seule une session authentifiée compte.
    if current_user is None:
        return False

    return bool(getattr(current_user, "is_authenticated", False))


def wants_json_response(req) -> bool:
    """True si la requête est un appel programmatique (AJAX/HTMX/JSON) et non une
    navigation navigateur classique.

    Sert à choisir la forme d'un refus d'accès : un appel programmatique doit
    recevoir un **401/403 JSON** (exploitable côté client), tandis qu'une
    navigation navigateur doit être **redirigée** vers la page de connexion.

    Détection :
    - en-tête ``HX-Request`` (HTMX) ;
    - en-tête ``X-Requested-With: XMLHttpRequest`` (fetch/jQuery) ;
    - négociation de contenu : le client préfère explicitement ``application/json``
      à ``text/html`` (un navigateur qui navigue envoie ``text/html`` en tête)."""
    if req.headers.get("HX-Request"):
        return True
    if req.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = req.accept_mimetypes
    json_q = accept["application/json"]
    return bool(json_q) and json_q >= accept["text/html"]


def is_socket_connection_authorized(flag_active: bool) -> bool:
    """Décision d'autorisation d'une connexion Socket.IO.

    - Si la sécurité du namespace est désactivée (``flag_active`` faux), la
      connexion est autorisée (comportement historique).
    - Sinon, elle exige une preuve d'identité valide : jeton applicatif
      (X-App-Token présenté à la poignée de main) ou session connectée. C'est la
      même règle que pour les routes REST (``is_authenticated_request``), ce qui
      referme la faille du header ``username`` (simple libellé, non prouvant)."""
    if not flag_active:
        return True
    return is_authenticated_request()


def require_app_token_or_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_authenticated_request():
            return func(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401

    return wrapper
