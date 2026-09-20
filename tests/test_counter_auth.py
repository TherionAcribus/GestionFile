"""Tests de protection par jeton des routes applicatives du comptoir.

Contexte
--------
Les routes ``/app/counter/*`` sont appelées par App_Comptoir et modifient
l'état du système (papier, staff, auto-calling, relance d'appel). Avant ce
correctif, elles n'étaient protégées que par le garde global ``before_request``,
lequel ne s'active QUE si ``SECURITY_LOGIN_COUNTER`` est vrai : lorsque cette
option est désactivée, un client non authentifié pouvait modifier un comptoir.

Le correctif ajoute ``@require_app_token_or_login`` directement sur chaque route
sensible, ce qui impose le jeton (ou une session connectée) de façon
inconditionnelle.

Deux niveaux de test, tous exécutables sans base de données ni serveur MySQL :

1. Comportement de la garde réelle (``auth_utils.require_app_token_or_login``)
   reproduite sur des vues factices ayant exactement le chemin/methode de
   chaque route protégée : rejet sans jeton (401), rejet jeton invalide (401),
   succès avec le bon jeton (200).
2. Régression statique : le code source de ``routes/counter.py`` porte bien le
   décorateur au-dessus de chaque handler applicatif sensible. Cela garantit que
   la protection ne disparaît pas par mégarde, même si l'app complète (qui exige
   MySQL) n'est pas importable dans l'environnement de test.

Note sur le périmètre par comptoir
-----------------------------------
Le jeton applicatif actuel est *global* : son payload ne contient qu'``exp``
(cf. ``generate_app_token`` dans ``app.py``) et le client ne transmet aucun
``counter_id`` à ``/api/get_app_token``. Il n'existe donc pas de « jeton par
comptoir ». Le critère « refuser une requête destinée à un autre comptoir (403) »
est explicitement conditionnel (« si des jetons par comptoir sont mis en place »)
et n'est pas encore applicable ; le test correspondant documente ce comportement.
"""

import os
import re
from datetime import datetime, timedelta

import jwt
import pytest
from flask import Flask, jsonify

# auth_utils est importable seul (contrairement à app.py qui exige MySQL).
from auth_utils import require_app_token_or_login, require_counter_access


SECRET_KEY = "test-secret-key-for-counter-auth"

# (chemin, methode, nom de la vue réelle dans routes/counter.py, decorateur)
# Les routes fusionnees web+App (point C12) portent require_counter_access :
# jeton/session inconditionnel sous /app/, session selon SECURITY_LOGIN_COUNTER
# sous /counter/. app_paper_add garde son propre chemin /app/ dedie.
PROTECTED_ROUTES = [
    ("/app/counter/paper_add", "POST", "app_paper_add", "require_app_token_or_login"),
    ("/app/counter/update_staff", "POST", "update_counter_staff", "require_counter_access"),
    ("/app/counter/auto_calling", "POST", "counter_auto_calling", "require_counter_access"),
    ("/app/counter/remove_staff", "POST", "counter_remove_staff", "require_counter_access"),
    ("/app/counter/relaunch_patient_call/1", "POST", "relaunch_patient_call", "require_counter_access"),
]


def _make_token(secret=SECRET_KEY, *, expired=False):
    delta = timedelta(minutes=-5) if expired else timedelta(hours=1)
    exp = datetime.utcnow() + delta
    return jwt.encode({"exp": exp}, secret, algorithm="HS256")


@pytest.fixture
def client():
    """Petite app Flask qui monte la garde réelle sur les mêmes chemins que
    les routes protégées, avec des vues factices renvoyant 200. On teste ainsi
    exactement le décorateur utilisé en production, sans dépendre de la base."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["TESTING"] = True

    def _make_view(endpoint):
        @require_app_token_or_login
        def _view(**kwargs):
            return jsonify({"ok": True, "endpoint": endpoint}), 200
        _view.__name__ = endpoint
        return _view

    # On enregistre une règle par (chemin, methode) protégés.
    app.add_url_rule("/app/counter/paper_add", "paper_add",
                     _make_view("paper_add"), methods=["POST"])
    app.add_url_rule("/app/counter/update_staff", "update_staff",
                     _make_view("update_staff"), methods=["POST"])
    app.add_url_rule("/app/counter/auto_calling", "auto_calling",
                     _make_view("auto_calling"), methods=["POST"])
    app.add_url_rule("/app/counter/remove_staff", "remove_staff",
                     _make_view("remove_staff"), methods=["POST"])
    app.add_url_rule("/app/counter/relaunch_patient_call/<int:counter_id>",
                     "relaunch", _make_view("relaunch"), methods=["POST"])

    return app.test_client()


@pytest.mark.parametrize("path,method,_view_name,_decorator", PROTECTED_ROUTES)
def test_missing_token_is_rejected(client, path, method, _view_name, _decorator):
    """Sans jeton ni session : 401."""
    resp = client.open(path, method=method)
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name,_decorator", PROTECTED_ROUTES)
def test_invalid_token_is_rejected(client, path, method, _view_name, _decorator):
    """Jeton signé avec une autre clé : 401."""
    bad = _make_token(secret="wrong-secret")
    resp = client.open(path, method=method, headers={"X-App-Token": bad})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name,_decorator", PROTECTED_ROUTES)
def test_garbage_token_is_rejected(client, path, method, _view_name, _decorator):
    """Chaîne quelconque comme jeton : 401."""
    resp = client.open(path, method=method, headers={"X-App-Token": "not-a-jwt"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name,_decorator", PROTECTED_ROUTES)
def test_expired_token_is_rejected(client, path, method, _view_name, _decorator):
    """Jeton expiré : 401."""
    expired = _make_token(expired=True)
    resp = client.open(path, method=method, headers={"X-App-Token": expired})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name,_decorator", PROTECTED_ROUTES)
def test_valid_token_is_accepted(client, path, method, _view_name, _decorator):
    """Jeton valide signé avec la bonne clé : la garde laisse passer (200)."""
    good = _make_token()
    resp = client.open(path, method=method, headers={"X-App-Token": good})
    assert resp.status_code == 200


def test_valid_token_accepted_for_any_counter_documents_no_per_counter_scope(client):
    """Documente l'absence de périmètre par comptoir.

    Le jeton étant global, un même jeton valide est accepté quel que soit le
    ``counter_id`` visé. Tant que des jetons par comptoir ne sont pas mis en
    place, aucun 403 « comptoir non autorisé » ne peut être renvoyé. Ce test
    fige le comportement actuel ; il devra être renforcé (attendre un 403 pour
    un comptoir hors périmètre) le jour où les jetons porteront un comptoir.
    """
    good = _make_token()
    for counter_id in (1, 2, 999):
        resp = client.post(f"/app/counter/relaunch_patient_call/{counter_id}",
                            headers={"X-App-Token": good})
        assert resp.status_code == 200


def test_real_routes_carry_the_decorator():
    """Régression : chaque route applicative sensible de routes/counter.py est
    bien décorée par le garde attendu (require_app_token_or_login pour les
    routes /app/ dédiées, require_counter_access pour les routes fusionnées
    web+App), juste au-dessus de son ``def``.

    Vérification statique sur le source car app.py n'est pas importable sans
    MySQL dans l'environnement de test."""
    counter_py = os.path.join(os.path.dirname(__file__), os.pardir, "routes", "counter.py")
    with open(counter_py, encoding="utf-8") as fh:
        source = fh.read()

    for _path, _method, view_name, decorator in PROTECTED_ROUTES:
        # Cherche le décorateur immédiatement suivi (éventuellement d'autres
        # décorateurs puis) de la def de la vue.
        pattern = re.compile(
            r"@" + decorator + r"\s*\n(?:\s*@[^\n]*\n)*\s*def\s+"
            + re.escape(view_name) + r"\s*\(",
        )
        assert pattern.search(source), (
            f"La vue {view_name} doit être décorée par "
            f"@{decorator} dans routes/counter.py"
        )


# --- require_counter_access : deux régimes selon le chemin -------------------
#
# Routes fusionnées web+App (point C12). La garde reproduit les protections
# des anciennes routes séparées :
#   - /app/...  : jeton ou session TOUJOURS, même SECURITY_LOGIN_COUNTER off ;
#   - /counter/... : session seulement quand SECURITY_LOGIN_COUNTER est actif.


@pytest.fixture
def dual_client():
    """Une route factice répondant sous les deux chemins, comme les vues
    fusionnées de routes/counter.py."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["TESTING"] = True
    app.config["SECURITY_LOGIN_COUNTER"] = False

    @app.route("/app/counter/double", methods=["POST"])
    @app.route("/counter/double", methods=["POST"])
    @require_counter_access
    def _view():
        return jsonify({"ok": True}), 200

    return app, app.test_client()


def test_app_path_requires_auth_even_when_security_off(dual_client):
    _app, client = dual_client
    assert client.post("/app/counter/double").status_code == 401


def test_app_path_accepts_valid_token(dual_client):
    _app, client = dual_client
    resp = client.post("/app/counter/double",
                       headers={"X-App-Token": _make_token()})
    assert resp.status_code == 200


def test_counter_path_open_when_security_off(dual_client):
    _app, client = dual_client
    assert client.post("/counter/double").status_code == 200


def test_counter_path_requires_session_when_security_on(dual_client):
    app, client = dual_client
    app.config["SECURITY_LOGIN_COUNTER"] = True
    assert client.post("/counter/double").status_code == 401


def test_counter_path_accepts_token_when_security_on(dual_client):
    app, client = dual_client
    app.config["SECURITY_LOGIN_COUNTER"] = True
    resp = client.post("/counter/double",
                       headers={"X-App-Token": _make_token()})
    assert resp.status_code == 200
