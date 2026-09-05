"""Point 2 (audit Admin) — /socket_admin restreint aux sessions admin.

Le namespace Socket.IO ``/socket_admin`` portait des évènements transversaux
(toasts, rafraîchissements de tableaux, changement de thème). Il n'exigeait
que ``is_authenticated_request()``, qui accepte **n'importe quel jeton
applicatif valide** (``X-App-Token``) — pas seulement une session admin.

Risque : une application comptoir/écran avec un jeton valide pouvait se
connecter à ``/socket_admin`` et recevoir des évènements admin.

Corrigé :
1. Nouvelle fonction ``is_admin_session()`` dans ``auth_utils.py`` : n'accepte
   qu'une session Flask authentifiée, **pas** de jeton applicatif.
2. ``connect_admin()`` dans ``sockets.py`` utilise ``is_admin_session()`` +
   vérifie que l'utilisateur a au moins une permission admin.

Tests :
- Unitaires pour ``is_admin_session()`` (sans MySQL, comme ``test_socket_auth``)
- Statiques pour ``sockets.py`` (vérifie l'usage de ``is_admin_session`` et la
  vérification de permission)
"""

import os
import re

import pytest

# ---------------------------------------------------------------------------
# Tests statiques (toujours exécutables, sans jwt ni MySQL)
# ---------------------------------------------------------------------------

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def test_sockets_imports_is_admin_session():
    """sockets.py doit importer is_admin_session depuis auth_utils."""
    source = _read("sockets.py")
    assert "is_admin_session" in source
    assert "from auth_utils import" in source
    assert "is_admin_session" in source


def test_connect_admin_uses_is_admin_session():
    """connect_admin() doit utiliser is_admin_session(), pas
    is_authenticated_request()."""
    source = _read("sockets.py")
    m = re.search(r"def connect_admin\(\):(.*?)(?=\n@|\ndef |\Z)", source, re.DOTALL)
    assert m, "connect_admin() introuvable"
    body = m.group(1)
    assert "is_admin_session()" in body
    # Ne doit plus utiliser is_authenticated_request() pour la décision principale.
    assert "is_authenticated_request()" not in body, (
        "connect_admin() ne doit plus utiliser is_authenticated_request() "
        "(accepte les jetons applicatifs génériques)"
    )


def test_connect_admin_checks_permission():
    """connect_admin() doit vérifier que l'utilisateur a au moins une
    permission admin."""
    source = _read("sockets.py")
    m = re.search(r"def connect_admin\(\):(.*?)(?=\n@|\ndef |\Z)", source, re.DOTALL)
    assert m, "connect_admin() introuvable"
    body = m.group(1)
    assert "user_has_permission" in body
    assert "PERMISSION_RESOURCES" in body


def test_auth_utils_has_is_admin_session():
    """auth_utils.py doit définir is_admin_session()."""
    source = _read("auth_utils.py")
    assert "def is_admin_session()" in source


def test_is_admin_session_does_not_check_app_token():
    """is_admin_session() ne doit pas vérifier X-App-Token dans son code
    (la docstring peut le mentionner pour expliquer la différence)."""
    source = _read("auth_utils.py")
    m = re.search(r"def is_admin_session\([^)]*\)\s*->\s*\w+:*(.*?)(?=^(?:def |@|\Z))", source, re.DOTALL | re.MULTILINE)
    assert m, "is_admin_session() introuvable"
    body = m.group(1)
    # Le code (hors docstring) ne doit pas référencer X-App-Token ni
    # verify_app_token. On retire la docstring avant de vérifier.
    code_only = re.sub(r'""".*?"""', '', body, flags=re.DOTALL)
    assert "X-App-Token" not in code_only
    assert "verify_app_token" not in code_only
    assert "request.headers.get" not in code_only


# ---------------------------------------------------------------------------
# Tests unitaires pour is_admin_session() — exigent jwt
# ---------------------------------------------------------------------------

try:
    import jwt as _jwt
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False

pytestmark_unit = pytest.mark.skipif(not _JWT_AVAILABLE, reason="jwt non installé")


if _JWT_AVAILABLE:
    from datetime import datetime, timedelta
    from flask import Flask
    from auth_utils import is_admin_session, is_authenticated_request

    SECRET_KEY = "test-secret-key-for-socket-admin-auth"

    @pytest.fixture
    def app():
        app = Flask(__name__)
        app.config["SECRET_KEY"] = SECRET_KEY
        return app

    def _token(secret=SECRET_KEY, *, expired=False):
        delta = timedelta(minutes=-5) if expired else timedelta(hours=1)
        return _jwt.encode({"exp": datetime.utcnow() + delta}, secret, algorithm="HS256")

    class TestIsAdminSessionRefusesAppToken:
        """Un jeton applicatif (X-App-Token) ne doit PAS suffire pour /socket_admin."""

        def test_refused_with_valid_app_token(self, app):
            """Un jeton valide est accepté par is_authenticated_request mais
            DOIT être refusé par is_admin_session."""
            with app.test_request_context("/", headers={"X-App-Token": _token()}):
                assert is_authenticated_request() is True
                assert is_admin_session() is False

        def test_refused_with_expired_app_token(self, app):
            with app.test_request_context("/", headers={"X-App-Token": _token(expired=True)}):
                assert is_admin_session() is False

        def test_refused_with_invalid_app_token(self, app):
            with app.test_request_context("/", headers={"X-App-Token": _token(secret="wrong")}):
                assert is_admin_session() is False

    class TestIsAdminSessionRefusesAnonymous:
        def test_refused_without_any_auth(self, app):
            with app.test_request_context("/"):
                assert is_admin_session() is False

        def test_refused_with_only_username_header(self, app):
            """Le header username seul ne prouve rien."""
            with app.test_request_context("/", headers={"username": "Counter 3"}):
                assert is_admin_session() is False

    class TestIsAdminSessionAcceptsAuthenticatedSession:
        def test_accepted_with_authenticated_user(self, app):
            """Simule un utilisateur authentifié via un mock de current_user."""
            class FakeUser:
                is_authenticated = True

            with app.test_request_context("/"):
                import auth_utils
                original = auth_utils.current_user
                auth_utils.current_user = FakeUser()
                try:
                    assert is_admin_session() is True
                finally:
                    auth_utils.current_user = original

        def test_refused_with_unauthenticated_user(self, app):
            class FakeUser:
                is_authenticated = False

            with app.test_request_context("/"):
                import auth_utils
                original = auth_utils.current_user
                auth_utils.current_user = FakeUser()
                try:
                    assert is_admin_session() is False
                finally:
                    auth_utils.current_user = original
