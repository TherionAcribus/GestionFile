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
from auth_utils import require_app_token_or_login


SECRET_KEY = "test-secret-key-for-counter-auth"

# (chemin, methode, nom de la vue réelle dans routes/counter.py)
# Les <int:counter_id> sont concrétisés pour le client de test.
PROTECTED_ROUTES = [
    ("/app/counter/paper_add", "POST", "app_paper_add"),
    ("/app/counter/update_staff", "POST", "app_update_counter_staff"),
    ("/app/counter/auto_calling", "POST", "app_auto_calling"),
    ("/app/counter/remove_staff", "POST", "app_remove_counter_staff"),
    ("/app/counter/relaunch_patient_call/1", "POST", "app_relaunch_patient_call"),
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


@pytest.mark.parametrize("path,method,_view_name", PROTECTED_ROUTES)
def test_missing_token_is_rejected(client, path, method, _view_name):
    """Sans jeton ni session : 401."""
    resp = client.open(path, method=method)
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name", PROTECTED_ROUTES)
def test_invalid_token_is_rejected(client, path, method, _view_name):
    """Jeton signé avec une autre clé : 401."""
    bad = _make_token(secret="wrong-secret")
    resp = client.open(path, method=method, headers={"X-App-Token": bad})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name", PROTECTED_ROUTES)
def test_garbage_token_is_rejected(client, path, method, _view_name):
    """Chaîne quelconque comme jeton : 401."""
    resp = client.open(path, method=method, headers={"X-App-Token": "not-a-jwt"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name", PROTECTED_ROUTES)
def test_expired_token_is_rejected(client, path, method, _view_name):
    """Jeton expiré : 401."""
    expired = _make_token(expired=True)
    resp = client.open(path, method=method, headers={"X-App-Token": expired})
    assert resp.status_code == 401


@pytest.mark.parametrize("path,method,_view_name", PROTECTED_ROUTES)
def test_valid_token_is_accepted(client, path, method, _view_name):
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
    bien décorée par @require_app_token_or_login, juste au-dessus de son ``def``.

    Vérification statique sur le source car app.py n'est pas importable sans
    MySQL dans l'environnement de test."""
    counter_py = os.path.join(os.path.dirname(__file__), os.pardir, "routes", "counter.py")
    with open(counter_py, encoding="utf-8") as fh:
        source = fh.read()

    for _path, _method, view_name in PROTECTED_ROUTES:
        # Cherche le décorateur immédiatement suivi (éventuellement d'autres
        # décorateurs puis) de la def de la vue.
        pattern = re.compile(
            r"@require_app_token_or_login\s*\n(?:\s*@[^\n]*\n)*\s*def\s+"
            + re.escape(view_name) + r"\s*\(",
        )
        assert pattern.search(source), (
            f"La vue {view_name} doit être décorée par "
            f"@require_app_token_or_login dans routes/counter.py"
        )
