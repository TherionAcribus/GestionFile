"""Tests des méthodes HTTP des routes mutatrices (point 2.1).

Objectif : aucune mutation d'état ne doit être accessible en **GET** (une simple
URL, donc déclenchable par CSRF / préchargement / crawler). Toutes les routes qui
suppriment, vident, (dés)activent, impriment/annoncent ou pilotent le lecteur
Spotify doivent être servies en **POST** ou **DELETE**.

Deux niveaux, exécutables **sans MySQL ni serveur** :

1. Régression statique : le décorateur ``@bp.route`` de chaque route convertie
   déclare le bon verbe (POST/DELETE) et **jamais GET**. (Importer les blueprints
   exigerait MySQL ; on scanne donc la source.)
2. Comportement réel : une petite app Flask reproduit une route POST-only et une
   route DELETE-only ; un GET renvoie **405** et la vue mutatrice **n'est pas
   exécutée** (aucune modification), conformément au test d'acceptation.
"""

import os
import re

import pytest
from flask import Flask, jsonify


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _route_methods(source, url):
    """Retourne la liste des méthodes déclarées pour le décorateur ``route(url,
    methods=[...])`` correspondant à l'URL EXACTE fournie, ou None si la route
    existe sans ``methods=`` (donc GET par défaut)."""
    esc = re.escape(url)
    # route('<url>', methods=[...])
    m = re.search(r"route\(\s*['\"]" + esc + r"['\"]\s*,\s*methods\s*=\s*\[([^\]]*)\]", source)
    if m:
        return [t.strip().strip("'\"").upper() for t in m.group(1).split(",") if t.strip()]
    # route('<url>')  sans methods -> GET implicite
    m = re.search(r"route\(\s*['\"]" + esc + r"['\"]\s*\)", source)
    if m:
        return None  # GET par défaut
    return "ABSENT"


# (fichier, url exacte, verbe attendu) pour chaque mutation convertie au point 2.1
_MUTATIONS = [
    ("routes/admin_staff.py", "/admin/staff/delete/<int:member_id>", "DELETE"),
    ("routes/admin_algo.py", "/admin/algo/toggle_activation", "POST"),
    ("routes/admin_algo.py", "/admin/algo/delete_rule/<int:algo_id>", "DELETE"),
    ("routes/admin_activity.py", "/admin/activity/delete/<int:activity_id>", "DELETE"),
    ("routes/admin_activity.py", "/admin/activity/delete/staff/<int:activity_id>", "DELETE"),
    ("routes/admin_security.py", "/admin/security/delete_role/<int:role_id>", "DELETE"),
    ("routes/admin_schedule.py", "/admin/schedule/delete/<int:schedule_id>", "DELETE"),
    ("routes/admin_counter.py", "/admin/counter/delete/<int:counter_id>", "DELETE"),
    ("routes/admin_translation.py", "/admin/languages/delete/<int:language_id>", "DELETE"),
    ("routes/admin_queue.py", "/admin/queue/delete_patient/<int:patient_id>", "DELETE"),
    ("routes/admin_queue.py", "/admin/database/clear_all_patients", "POST"),
    ("routes/admin_queue.py", "/admin/database/clear_all_patients_with_saving", "POST"),
    ("routes/admin_patient.py", "/admin/patient/delete_button/<int:button_id>", "DELETE"),
    ("routes/admin_patient.py", "/admin/patient/delete_button_image/<int:button_id>", "DELETE"),
    ("routes/admin_patient.py", "/admin/patient/print_test_ticket_size", "POST"),
    ("routes/admin_patient.py", "/admin/patient/print_ticket_test", "POST"),
    ("routes/admin_patient.py", "/admin/button/deactivate/<int:button_id>", "POST"),
    ("routes/admin_patient.py", "/admin/button/activate/<int:button_id>", "POST"),
    ("routes/admin_music.py", "/spotify/shuffle", "POST"),
    ("routes/admin_music.py", "/spotify/pause_music", "POST"),
    ("routes/admin_music.py", "/spotify/resume_music", "POST"),
    ("routes/admin_music.py", "/spotify/next_track", "POST"),
    ("routes/admin_music.py", "/spotify/previous_track", "POST"),
    ("routes/admin_announce.py", "/admin/announce/audio/test/<string:scope>", "POST"),
]


@pytest.mark.parametrize("rel,url,verb", _MUTATIONS,
                         ids=[f"{u}->{v}" for _, u, v in _MUTATIONS])
def test_mutation_route_is_not_get(rel, url, verb):
    methods = _route_methods(_read(rel), url)
    assert methods != "ABSENT", f"route {url} introuvable dans {rel}"
    assert methods is not None, f"route {url} servie en GET implicite (aucun methods=)"
    assert "GET" not in methods, f"route {url} accepte encore GET : {methods}"
    assert verb in methods, f"route {url} devrait accepter {verb} : {methods}"


def test_get_delete_user_route_removed():
    """L'ancienne route GET ``/admin/security/delete_user`` (doublon NON protégé,
    contournait la garde 'dernier admin') a été retirée : seule subsiste la route
    POST protégée."""
    source = _read("routes/admin_security.py")
    # Toutes les occurrences de la route delete_user doivent être en POST.
    decls = re.findall(
        r"route\(\s*['\"]/admin/security/delete_user/<int:user_id>['\"]\s*,\s*methods\s*=\s*\[([^\]]*)\]",
        source)
    assert decls, "la route POST delete_user doit subsister"
    for methods in decls:
        upper = methods.upper()
        assert "GET" not in upper, "plus aucune route GET delete_user ne doit exister"
        assert "POST" in upper
    # Aucune déclaration sans methods= (GET implicite) ne doit exister non plus.
    assert not re.search(
        r"route\(\s*['\"]/admin/security/delete_user/<int:user_id>['\"]\s*\)", source)


# --- Comportement réel : GET sur une mutation -> 405 et aucune modification ---

def _make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    state = {"deleted": 0, "cleared": 0}

    @app.route("/thing/<int:tid>", methods=["DELETE"])
    def _delete_thing(tid):
        state["deleted"] += 1
        return jsonify({"ok": True})

    @app.route("/clear", methods=["POST"])
    def _clear():
        state["cleared"] += 1
        return jsonify({"ok": True})

    app.config["_state"] = state
    return app


@pytest.fixture
def app():
    return _make_app()


def test_get_on_delete_route_returns_405_and_no_mutation(app):
    client = app.test_client()
    resp = client.get("/thing/1")
    assert resp.status_code == 405
    assert app.config["_state"]["deleted"] == 0  # aucune modification


def test_get_on_post_route_returns_405_and_no_mutation(app):
    client = app.test_client()
    resp = client.get("/clear")
    assert resp.status_code == 405
    assert app.config["_state"]["cleared"] == 0


def test_correct_verb_executes_mutation(app):
    client = app.test_client()
    assert client.delete("/thing/1").status_code == 200
    assert app.config["_state"]["deleted"] == 1
    assert client.post("/clear").status_code == 200
    assert app.config["_state"]["cleared"] == 1
