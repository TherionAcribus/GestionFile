from __future__ import annotations

import hmac
from functools import wraps

import jwt
from flask import current_app, has_request_context, jsonify, request

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


def is_authenticated_request() -> bool:
    if not has_request_context():
        return False

    token = request.headers.get("X-App-Token")
    if token and verify_app_token(token):
        return True

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
