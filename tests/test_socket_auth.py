"""Tests de l'autorisation des connexions Socket.IO.

Couvre ``auth_utils.is_socket_connection_authorized``, utilisée par le garde
``_socket_require`` des namespaces (dont ``/socket_app_counter``) :
- sécurité désactivée => connexion autorisée (comportement historique) ;
- sécurité active => jeton applicatif valide requis (le header ``username`` seul
  ne prouve rien) ; connexion refusée sans jeton ou avec un jeton invalide.

Importable/exécutable sans MySQL (auth_utils n'importe pas app.py). On utilise un
contexte de requête Flask minimal pour fournir/omettre l'en-tête X-App-Token.
"""

from datetime import datetime, timedelta

import jwt
import pytest
from flask import Flask

from auth_utils import is_socket_connection_authorized


SECRET_KEY = "test-secret-key-for-socket-auth"


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    return app


def _token(secret=SECRET_KEY, *, expired=False):
    delta = timedelta(minutes=-5) if expired else timedelta(hours=1)
    return jwt.encode({"exp": datetime.utcnow() + delta}, secret, algorithm="HS256")


def test_allowed_when_security_disabled(app):
    # Flag inactif : autorisé même sans aucun en-tête.
    with app.test_request_context("/"):
        assert is_socket_connection_authorized(False) is True


def test_allowed_with_valid_token_when_security_active(app):
    with app.test_request_context("/", headers={"X-App-Token": _token()}):
        assert is_socket_connection_authorized(True) is True


def test_refused_without_token_when_security_active(app):
    # Seulement un username : ne prouve rien => refusé.
    with app.test_request_context("/", headers={"username": "Counter 3 App"}):
        assert is_socket_connection_authorized(True) is False


def test_refused_with_invalid_token_when_security_active(app):
    with app.test_request_context("/", headers={"X-App-Token": _token(secret="wrong")}):
        assert is_socket_connection_authorized(True) is False


def test_refused_with_expired_token_when_security_active(app):
    with app.test_request_context("/", headers={"X-App-Token": _token(expired=True)}):
        assert is_socket_connection_authorized(True) is False
